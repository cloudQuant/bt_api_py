"""Small offline behavior tests for the artifact-only LOAD01 matrix consumer."""
from __future__ import annotations

import ast
import hashlib
import json
import tempfile
from typing import Mapping
import unittest
from copy import deepcopy
from pathlib import Path

from scripts import load01_matrix_consumer as consumer


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = json.loads((REPOSITORY_ROOT / "docs/evidence/iteration10-load01-engineering-registration-20261018/threshold-registration-r2.json").read_text(encoding="utf-8"))
MIB = 1024 * 1024


def _bound_registration() -> tuple[dict, dict]:
    registration = deepcopy(REGISTRATION)
    fake_pin = "a" * 64
    fake_head = "b" * 40
    source_pins = {}
    report_source_bindings = {}
    for index, alias in enumerate(sorted(consumer.SOURCE_ALIASES), start=1):
        _, expected_path = consumer.SOURCE_ALIAS_MODULES_AND_PATHS[alias]
        report_key = consumer.SOURCE_ALIAS_REPORT_KEYS[alias]
        pin = {"path": expected_path, "sha256": f"{index:064x}", "bytes": index}
        source_pins[alias] = dict(pin)
        report_source_bindings[report_key] = dict(pin)
    # The report binds additional fixed modules and focused tests; these are
    # authenticated by the complete external receipt, not semantic aliases.
    for index, report_key in enumerate(
        (
            "bt_api_py.service_runtime",
            "tests.unit._load01_resource_diagnostics",
            "tests.unit._service_group_provider_fixtures",
            "tests.integration._dim09_three_leg_fixtures",
            "load_n5_report_tests",
            "load_n5_group_workload_tests",
            "load01_threshold_registration",
        ),
        start=100,
    ):
        report_source_bindings[report_key] = {
            "path": f"tests/unit/fixed-source-{index}.py",
            "sha256": f"{index:064x}",
            "bytes": index,
        }
    trusted = {
        "status": "BOUND",
        "registration_sha256": fake_pin,
        "sdk_head": fake_head,
        "source_pins": source_pins,
        "report_source_bindings": report_source_bindings,
    }
    return registration, trusted


def _recovery_report(profile_id: str, profile: dict) -> dict:
    streams, subscriptions = consumer._profile_streams(REGISTRATION, profile)
    incidents = []
    by_window: dict[int, set[int]] = {}
    for window in range(1, 6):
        indices = {
            account[(window - 1) % len(account)]
            for account in profile["orders"]["account_strategy_indices_by_account"]
            if account
        }
        by_window[window] = indices
        for index in sorted(indices):
            incidents.append(("reconnect", index, window))
    incidents.append(("slow_consumer", 0, None))
    stream_count = sum(len(subscriptions[index]) for _, index, _ in incidents)
    rows = []
    for ordinal, (kind, index, window) in enumerate(incidents, 1):
        fault_start = 1_000_000_000 + ordinal * 100_000_000
        fault_end = fault_start + 10_000_000
        capture_start = fault_end + 1_000_000
        capture_end = capture_start + 100_000_000
        deadline = fault_end + 10_000_000_000
        incident_streams = {}
        for topic_index in sorted(subscriptions[index]):
            stream_id = streams[topic_index]["stream_id"]
            receipt_ns = capture_end + 1_000_000
            incident_streams[stream_id] = {
                "stream_id": stream_id,
                "head_target_epoch": "epoch-1",
                "head_target_sequence": 10,
                "head_captured_monotonic_ns": capture_end,
                "first_qualifying_receipt": {
                    "epoch": "epoch-1",
                    "sequence": 10,
                    "received_monotonic_ns": receipt_ns,
                },
                "target_timing_deadline_monotonic_ns": deadline,
                "target_timing_status": "WITHIN_10S",
                "target_timing_elapsed_ms": (receipt_ns - fault_end) / 1_000_000,
                "target_receipt_observed": True,
                "target_reached": True,
                "snapshot_skipped_sequence_count": 0,
                "gap_coverage_status": "COMPLETE_TO_TARGET",
                "coverage_gap_event_count": 0,
            }
        count = len(incident_streams)
        rows.append({
            "kind": kind,
            "incident_id": f"incident-{ordinal:03d}",
            "profile_id": profile_id,
            "strategy_index": index,
            "window_number": window,
            "fault_started_monotonic_ns": fault_start,
            "fault_ended_monotonic_ns": fault_end,
            "head_capture_started_monotonic_ns": capture_start,
            "head_capture_completed_monotonic_ns": capture_end,
            "head_capture_budget_deadline_monotonic_ns": deadline,
            "head_capture_budget_elapsed_ms": (capture_end - fault_end) / 1_000_000,
            "head_capture_budget_status": "WITHIN_10S",
            "head_capture_timeout_status": "NO_TIMEOUT",
            "head_capture_elapsed_ms": (capture_end - capture_start) / 1_000_000,
            "head_rpc_count": count,
            "head_rpc_error_count": 0,
            "head_maintenance_check_count": count,
            "head_maintenance_error_count": 0,
            "target_stream_count": count,
            "target_receipt_count": count,
            "target_reached_count": count,
            "target_timing_status": "WITHIN_10S_FOR_ALL_TARGETS",
            "gap_coverage_status": "COMPLETE_TO_TARGET",
            "snapshot_skipped_sequence_count": 0,
            "snapshot_skipped_sequence_known_stream_count": count,
            "snapshot_skipped_sequence_unknown_stream_count": 0,
            "streams": incident_streams,
        })
    reconnect_target_count = sum(len(subscriptions[index]) for values in by_window.values() for index in values)
    slow_target_count = len(subscriptions[0])
    expected_by_kind = {
        "slow_consumer": (1, slow_target_count),
        "reconnect": (50, reconnect_target_count),
    }
    by_kind = {}
    for kind, (incident_count, target_count) in expected_by_kind.items():
        by_kind[kind] = {
            "incident_count": incident_count,
            "incident_count_expected_from_observed_faults": incident_count,
            "target_stream_count": target_count,
            "target_stream_count_expected": target_count,
            "target_receipt_count": target_count,
            "target_timing_within_10s_count": target_count,
            "target_timing_exceeded_10s_count": 0,
            "target_timing_status": "WITHIN_10S_FOR_ALL_TARGETS",
        }
    return {
        "incidents": rows,
        "profile_counts": {
            profile_id: {
                "by_kind": by_kind,
                "reconnect_window_counts": {str(window): 10 for window in range(1, 6)},
            }
        },
        "profile_identity_missing_incident_count": 0,
        "reconnect_window_invalid_incident_count": 0,
        "incident_count": len(rows),
        "incident_count_expected_from_observed_faults": len(rows),
        "head_rpc_count": stream_count,
        "head_rpc_count_expected_from_observed_faults": stream_count,
        "head_rpc_error_count": 0,
        "target_timing_deadline_ms": 10_000,
        "target_stream_count": stream_count,
        "target_receipt_count": stream_count,
        "target_reached_count": stream_count,
        "target_timing_within_10s_count": stream_count,
        "target_timing_exceeded_10s_count": 0,
        "target_timing_status": "WITHIN_10S_FOR_ALL_TARGETS",
        "snapshot_skipped_sequence_count": 0,
        "snapshot_skipped_sequence_known_stream_count": stream_count,
        "snapshot_skipped_sequence_unknown_stream_count": 0,
        "gap_coverage_incomplete_incident_count": 0,
        "unrecovered_gap_count": 0,
        "unrecovered_gap_event_count": 0,
        "gap_coverage_status": "COMPLETE_TO_TARGET",
    }


def _valid_n1_report() -> tuple[dict, dict, dict]:
    registration, trusted = _bound_registration()
    profile_id = "N1"
    profile = registration["workload_contract"]["profiles"][profile_id]
    duration = registration["workload_contract"]["full_duration_seconds"]
    streams, subscriptions = consumer._profile_streams(registration, profile)
    metrics = {
        "profile_id": profile_id,
        "mode": "full",
        "registration": {"sha256": trusted["registration_sha256"]},
        "input_messages_by_topic": {},
        "delivered_messages_by_strategy_and_topic": {},
        "order_cycles_by_account": {},
        "order_rpc_by_strategy": {},
        "market_end_to_end_by_strategy": {},
        "system_cpu": {
            "status": "COMPLETE",
            "schedule_complete": True,
            "sample_count": 360,
            "expected_sample_count": 360,
            "coverage_fraction": 1,
            "mean_percent": 25,
            "p95_percent": 35,
        },
    }
    for topic_index, rate in enumerate(profile["market"]["input_messages_per_second_by_topic"]):
        metrics["input_messages_by_topic"][f"K{topic_index:02d}"] = {
            "target_per_second": rate,
            "observed_source_sequence": rate * duration,
        }
    for index in range(100):
        metrics["delivered_messages_by_strategy_and_topic"][f"strategy-{index:03d}"] = {
            f"K{topic:02d}": {
                "target_per_second": profile["market"]["input_messages_per_second_by_topic"][topic],
                "observed_delivery_count": profile["market"]["input_messages_per_second_by_topic"][topic] * duration,
            }
            for topic in subscriptions[index]
        }
        metrics["market_end_to_end_by_strategy"][f"strategy-{index:03d}"] = {
            "status": "OBSERVED",
            "percentiles_ms": {"p50": 10, "p95": 20, "p99": 30},
        }
        metrics["order_cycles_by_account"][f"account-{index // 10:02d}"] = {
            "observed_order_cycles": round(profile["orders"]["per_account_logical_order_cycles_per_second"][index // 10] * duration),
        }
        expected_ops = consumer._expected_rpc_counts({**profile, "_matrix_profile_id": profile_id}, index, duration)
        metrics["order_rpc_by_strategy"][f"strategy-{index:03d}"] = {}
        for operation, count in expected_ops.items():
            sample_count = min(count, 2048)
            metrics["order_rpc_by_strategy"][f"strategy-{index:03d}"][operation] = {
                "observed_count": count,
                "scheduled_count": count,
                "error_count": 0,
                "timeout_count": 0,
                "sampled_count": sample_count,
                "sample_capacity": 2048 if count else 0,
                "sample_coverage_fraction": sample_count / count if count else 0,
                "full_population_retained": count <= 2048,
                "percentiles_ms_nearest_rank": {"p50": 10, "p95": 20, "p99": 30} if count else None,
            }
    for account_index, rate in enumerate(profile["orders"]["per_account_logical_order_cycles_per_second"]):
        metrics["order_cycles_by_account"][f"account-{account_index:02d}"] = {
            "observed_order_cycles": round(rate * duration),
        }

    expected_children = registration["workload_contract"]["total_child_processes_by_profile"][profile_id]
    projected = expected_children * 72 * MIB
    reserve = registration["workload_contract"]["minimum_memory_reserve_bytes"]
    gate = projected + reserve
    report = {
        "load_profile": profile_id,
        "mode": "full",
        "state": "LOAD_PROFILE_COMPLETE",
        "fixed_parent_commit": trusted["sdk_head"],
        "source_bindings": trusted["report_source_bindings"],
        "load": {
            "requested_duration_seconds": duration,
            "actual_duration_seconds": duration,
            "p0_evaluation_eligible": False,
        },
        "registered_performance_metrics": metrics,
        "resource_samples": [
            {"load01_cpu_steady_load_sample": True, "load01_cpu_sample_tick": tick, "database_tree_bytes": 1}
            for tick in range(1, 361)
        ],
        "market_queue_snapshot_final": {},
        "durable_monitor": {"sqlite_bytes_after_close": 1},
        "service_registry_sessions_after_close": {"market": 0, "monitor-durable": 0},
        "normal_shutdown_complete": True,
        "topology": {"total_child_processes": expected_children},
        "preflight": {
            "projected_all_child_rss_bytes": projected,
            "required_available_for_start_bytes": gate,
            "available_memory_bytes": gate,
        },
        "process_lifecycle": {
            "expected_child_count": expected_children,
            "actual_child_count": expected_children,
            "normal_exit_count": expected_children,
            "all_child_exit_codes_zero": True,
            "forced_termination_count": 0,
        },
        "children": [{"normal_exit": True, "closed": True, "exit_code": 0} for _ in range(expected_children)],
        "cleanup": {"errors": [], "termination_requests": []},
        "market": {
            "slow_consumer": {"pause_started_seconds": 30, "pause_ended_seconds": 45},
            "recovery_timing": _recovery_report(profile_id, profile),
        },
        "market_terminal_catchup": {
            "state": "complete",
            "catchup_elapsed_ms": 100,
            "strategy_results": {f"strategy-{index:03d}": {"complete": True, "failures": []} for index in range(100)},
            "failure_codes_by_strategy": {},
        },
    }
    return registration, trusted, report


def _valid_n5_group_projection() -> tuple[dict, dict]:
    profile = REGISTRATION["workload_contract"]["profiles"]["N5"]
    contract = profile["orders"]["n5_group_contract"]
    capable = set(contract["group_capable_strategy_indices"])
    requests = contract["per_account_1800_second_targets"]["group_requests"]
    children = contract["per_account_1800_second_targets"]["group_child_dispatches"]
    legs = ("leg-put", "leg-future", "leg-call")
    quantities = {"leg-put": "2", "leg-future": "8", "leg-call": "4"}
    account_by_strategy = {
        index: account_index
        for account_index, indexes in enumerate(profile["orders"]["account_strategy_indices_by_account"])
        for index in indexes
    }
    rows = {}
    intent_order_join_by_account = {f"account-{index:02d}": [] for index in range(10)}
    for index in range(100):
        is_capable = index in capable
        account_index = account_by_strategy[index]
        account_ref = f"account-{account_index:02d}"
        source_identity = contract["source_identity_by_report_account"][account_ref]
        source_account_ref = source_identity["account_ref"]
        source_strategy_ref = source_identity["group_strategy_ref"]
        expected_req = requests if is_capable else 0
        expected_children = children if is_capable else 0
        expected_account_logical = {source_account_ref: expected_req} if is_capable else {}
        expected_account_children = {source_account_ref: expected_children} if is_capable else {}
        expected_leg_children = {leg: requests if is_capable else 0 for leg in legs}
        route_intents = {
            leg: [f"child-{index}-{leg}-{ordinal}" for ordinal in range(expected_req)]
            for leg in legs
        }
        routes = {
            leg: {
                "account_ref": source_account_ref if is_capable else None,
                "strategy_ref": source_strategy_ref if is_capable else None,
                "portfolio_ref": f"portfolio-{index:03d}" if is_capable else None,
                "scope_ref": f"scope-{index:03d}" if is_capable else None,
                "expected_dispatch_count": requests if is_capable else 0,
                "observed_dispatch_count": requests if is_capable else 0,
                "observed_dispatch_count_source": "distinct_durable_filled_child_intent_observation_not_provider_write_log" if is_capable else None,
                "intent_ids": route_intents[leg],
            }
            for leg in legs
        }
        durable_records = []
        if is_capable:
            for ordinal in range(expected_req):
                durable_records.append({
                    "query_found": True,
                    "group_id": f"group-{index}-{ordinal}",
                    "version": 3,
                    "revision": ordinal + 1,
                    "state": "PAIRED",
                    "portfolio_ref": f"portfolio-{index:03d}",
                    "strategy_ref": source_strategy_ref,
                    "created_at_ns": ordinal + 1,
                    "updated_at_ns": ordinal + 2,
                    "leg_intent_ids": {leg: route_intents[leg][ordinal] for leg in legs},
                })
                if is_capable:
                    for leg in legs:
                        client_order_id = f"provider-client-order-{account_by_strategy[index]:02d}-{ordinal:03d}-{leg}"
                        intent_order_join_by_account[account_ref].append({
                            "group_id": f"group-{index}-{ordinal}",
                            "leg_id": leg,
                            "intent_id": route_intents[leg][ordinal],
                            "attempt_started_count": 1,
                            "attempt_started_revision": ordinal + 1,
                            "attempt_started_record_sha256": "a" * 64,
                            "attempt_binding_valid": True,
                            "client_order_id": client_order_id,
                            "provider_write_count": 1,
                            "provider_order_row_present": True,
                            "order_status": "filled",
                            "order_query_count": 1,
                            "fill_query_count": 1,
                            "join_state": "joined",
                        })
        rpc_ops = {}
        for operation in ("groups.submit", "groups.query"):
            rpc_ops[operation] = {
                "expected_count": expected_req,
                "observed_count": expected_req,
                "error_count": 0,
                "timeout_count": 0,
                "p50_ns": 1_000_000,
                "p95_ns": 2_000_000,
                "p99_ns": 3_000_000,
                "p50_ms": 1.0,
                "p95_ms": 2.0,
                "p99_ms": 3.0,
            }
        rows[f"strategy-{index:03d}"] = {
            "expected": {
                "groups.submit": expected_req,
                "groups.query": expected_req,
                "group_logical_count": expected_req,
                "group_child_dispatch_count": expected_children,
                "group_logical_count_by_account": expected_account_logical,
                "group_child_dispatch_count_by_account": expected_account_children,
                "group_child_dispatch_count_by_leg": expected_leg_children,
            },
            "observed": {
                "group_logical_count": expected_req,
                "group_success_count": expected_req,
                "group_failure_count": 0,
                "group_unresolved_count": 0,
                "group_child_dispatch_count": expected_children,
            },
            "expected_logical_group_count": expected_req,
            "observed_logical_group_count": expected_req,
            "expected_child_dispatch_count": expected_children,
            "observed_child_dispatch_count": expected_children,
            "group_child_dispatch_count_source": "distinct_durable_filled_child_intent_observation_not_provider_write_log" if is_capable else None,
            "group_child_provider_write_count": None,
            "group_child_provider_write_count_status": "NOT_REGISTERED_FROM_OWNER_LOG",
            "group_logical_count_by_account": expected_account_logical,
            "group_child_dispatch_count_by_account": expected_account_children,
            "group_child_dispatch_count_by_leg": expected_leg_children,
            "group_child_outcome_by_leg": {
                leg: {"READY": 0, "REQUESTING": 0, "UNKNOWN": 0, "OBSERVED": requests if is_capable else 0}
                for leg in legs
            },
            "group_child_execution_state_by_leg": {
                leg: {"FILLED": requests if is_capable else 0, "UNKNOWN": 0, "DUPLICATE": 0, "UNRESOLVED": 0}
                for leg in legs
            },
            "group_child_routes_by_leg": routes,
            "group_durable_record_count": expected_req,
            "group_durable_records": durable_records,
            "group_rpc_metrics": {"operations": rpc_ops},
            "group_lifecycle_ms": {
                "count": expected_req,
                "p50": 5.0 if is_capable else None,
                "p95": 8.0 if is_capable else None,
                "p99": 9.0 if is_capable else None,
                "max": 10.0 if is_capable else None,
            },
            "group_child_dispatch_latency_status": "NOT_REGISTERED_BY_COORDINATOR_SNAPSHOT",
            "group_child_dispatch_latency_ns_by_account_strategy": None,
        }
    provider_ledger_by_account = {
        f"account-{account_index:02d}": {
            "attribution": "n5_group_proof_runtime_only",
            "account_ref": contract["source_identity_by_report_account"][
                f"account-{account_index:02d}"
            ]["account_ref"],
            "strategy_ref": contract["source_identity_by_report_account"][
                f"account-{account_index:02d}"
            ]["group_strategy_ref"],
            "strategy_index": account_index * 10,
            "make_order_write_count": children,
            "provider_order_count": children,
            "unique_client_order_id_count": children,
            "duplicate_client_order_id_count": 0,
            "write_without_order_record_count": 0,
            "order_record_without_write_count": 0,
            "unjoined_write_count": 0,
            "unjoined_make_order_write_count": 0,
            "unjoined_client_order_ids": [],
            "duplicate_join_client_order_id_count": 0,
            "duplicate_join_client_order_ids": [],
            "unjoined_query_count": 0,
            "order_query_count": children,
            "fill_query_count": children,
            "order_status_counts": {"filled": children},
            "writes_by_client_order_id": [
                {
                    "client_order_id": join_row["client_order_id"],
                    "write_count": 1,
                    "order_status": "filled",
                    "filled": quantities[join_row["leg_id"]],
                    "quantity": quantities[join_row["leg_id"]],
                }
                for join_row in intent_order_join_by_account[f"account-{account_index:02d}"]
            ],
        }
        for account_index in range(10)
    }
    return profile, {
        "accounts_and_execution": {
            "n5_group_metrics": {
                "by_strategy": rows,
                "provider_ledger_by_account": provider_ledger_by_account,
                "intent_order_join_by_account": intent_order_join_by_account,
                "owner_join_counts_by_account": {
                    account_ref: {
                        "intent_order_join_count": len(join_rows),
                        "joined_intent_order_count": sum(row["join_state"] == "joined" for row in join_rows),
                    }
                    for account_ref, join_rows in intent_order_join_by_account.items()
                },
            }
        }
    }


class MatrixConsumerTests(unittest.TestCase):
    def test_registration_keeps_all_profiles_and_correct_n5_memory_gate(self) -> None:
        self.assertEqual([], consumer.validate_registration(REGISTRATION))

    def test_normal_n1_report_passes_without_p0_eligibility(self) -> None:
        registration, trusted, report = _valid_n1_report()
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual("NOT_CLAIMED_BY_CONSUMER", result["formal_acceptance_status"])

    def test_missing_recovery_stream_is_incomplete(self) -> None:
        registration, trusted, report = _valid_n1_report()
        incident = report["market"]["recovery_timing"]["incidents"][0]
        incident["streams"].pop(next(iter(incident["streams"])))
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("INCOMPLETE", result["status"], result)
        self.assertTrue(any("recovery_stream_coverage_mismatch" in item for item in result["incomplete_reasons"]))

    def test_snapshot_skip_stays_incomplete_after_head_receipt(self) -> None:
        registration, trusted, report = _valid_n1_report()
        incident = report["market"]["recovery_timing"]["incidents"][0]
        stream = next(iter(incident["streams"].values()))
        stream["snapshot_skipped_sequence_count"] = 1
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("INCOMPLETE", result["status"], result)
        self.assertTrue(any("recovery_snapshot_skip_incomplete" in item for item in result["incomplete_reasons"]))

    def test_market_percentiles_are_observations_but_must_be_complete(self) -> None:
        registration, trusted, report = _valid_n1_report()
        percentiles = report["registered_performance_metrics"]["market_end_to_end_by_strategy"]["strategy-037"]["percentiles_ms"]
        percentiles.update({"p50": 51, "p95": 251, "p99": 1001})
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual({"p50": 51, "p95": 251, "p99": 1001}, percentiles)

        percentiles.pop("p95")
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("INCOMPLETE", result["status"], result)
        self.assertTrue(any("market_latency_invalid:37:p95" in item for item in result["incomplete_reasons"]))

    def test_order_rpc_reports_p50_p95_and_keeps_the_five_second_gate(self) -> None:
        registration, trusted, report = _valid_n1_report()
        operation = report["registered_performance_metrics"]["order_rpc_by_strategy"][
            "strategy-037"
        ]["orders.submit"]
        operation["percentiles_ms_nearest_rank"] = {
            "p50": 300,
            "p95": 2500,
            "p99": 5000,
        }
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("PASS", result["status"], result)
        self.assertEqual(
            {"p50": 300, "p95": 2500, "p99": 5000},
            operation["percentiles_ms_nearest_rank"],
        )

        operation["percentiles_ms_nearest_rank"]["p99"] = 5001
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("FAIL", result["status"], result)
        self.assertIn(
            "order_rpc_latency_exceeded:37:orders.submit:p99",
            result["failures"],
        )

    def test_order_rpc_timeout_remains_a_hard_failure(self) -> None:
        registration, trusted, report = _valid_n1_report()
        operation = report["registered_performance_metrics"]["order_rpc_by_strategy"][
            "strategy-037"
        ]["orders.submit"]
        operation["timeout_count"] = 1
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("FAIL", result["status"], result)
        self.assertIn("ordinary_order_rpc_timeout:37:orders.submit", result["failures"])

    def test_unbound_source_pins_cannot_pass(self) -> None:
        registration, trusted, report = _valid_n1_report()
        trusted["status"] = "PENDING"
        result = consumer.evaluate_profile(registration, report, trusted, "N1")
        self.assertEqual("INCOMPLETE", result["status"], result)
        self.assertIn(
            "incomplete:external_trusted_source_binding_not_bound",
            result["incomplete_reasons"],
        )

    def test_matrix_selects_all_profiles_while_p0_stays_u0_only(self) -> None:
        result = consumer.evaluate_matrix(REGISTRATION, {}, None)
        self.assertEqual(list(consumer.PROFILE_IDS), list(result["profiles"]))
        self.assertEqual(["U0"], result["p0_baseline_profiles"])
        self.assertEqual("NOT_CLAIMED_BY_CONSUMER", result["formal_acceptance_status"])

    def test_n5_group_rows_require_durable_observed_children_and_separate_rpc_samples(self) -> None:
        profile, report = _valid_n5_group_projection()
        incomplete: list[str] = []
        failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertEqual([], incomplete)
        self.assertEqual([], failures)

    def test_n5_group_rpc_outlier_fails_its_own_strategy_operation(self) -> None:
        profile, report = _valid_n5_group_projection()
        operation = report["accounts_and_execution"]["n5_group_metrics"]["by_strategy"]["strategy-000"]["group_rpc_metrics"]["operations"]["groups.query"]
        operation["p50_ns"] = 300_000_000
        operation["p95_ns"] = 2_500_000_000
        operation["p99_ns"] = 5_000_000_000
        operation["p50_ms"] = 300.0
        operation["p95_ms"] = 2500.0
        operation["p99_ms"] = 5000.0
        operation["p99_ns"] = 5_001_000_000
        operation["p99_ms"] = 5001.0
        incomplete: list[str] = []
        failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertTrue(any("n5_group_rpc_latency_exceeded:0:groups.query:p99" in item for item in failures))

    def test_n5_group_rpc_p50_p95_are_observations_and_timeout_stays_hard(self) -> None:
        profile, report = _valid_n5_group_projection()
        operation = report["accounts_and_execution"]["n5_group_metrics"]["by_strategy"][
            "strategy-000"
        ]["group_rpc_metrics"]["operations"]["groups.query"]
        operation.update(
            {
                "p50_ns": 300_000_000,
                "p95_ns": 2_500_000_000,
                "p99_ns": 5_000_000_000,
                "p50_ms": 300.0,
                "p95_ms": 2500.0,
                "p99_ms": 5000.0,
            }
        )
        incomplete: list[str] = []
        failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertEqual([], incomplete)
        self.assertEqual([], failures)

        operation["timeout_count"] = 1
        incomplete = []
        failures = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertIn("n5_group_rpc_timeout:0:groups.query", failures)

    def test_n5_group_unknown_leg_phase_fails_even_when_durable_count_matches(self) -> None:
        profile, report = _valid_n5_group_projection()
        phases = report["accounts_and_execution"]["n5_group_metrics"]["by_strategy"]["strategy-000"]["group_child_outcome_by_leg"]["leg-put"]
        phases["OBSERVED"] -= 1
        phases["UNKNOWN"] += 1
        incomplete: list[str] = []
        failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertTrue(any("n5_group_child_outcome_not_observed:0:leg-put" in item for item in failures))

    def test_consumer_source_numeric_registration_and_n5_provider_edges_fail_closed(self) -> None:
        with self.subTest("externally pinned registration does not need an embedded self hash"):
            registration, trusted, report = _valid_n1_report()
            self.assertNotIn("final_source_binding", registration["source_scope"])
            self.assertEqual("PASS", consumer.evaluate_profile(registration, report, trusted, "N1")["status"])
            embedded = deepcopy(registration)
            embedded["source_scope"]["final_source_binding"] = {
                "status": "BOUND",
                "registration_sha256": trusted["registration_sha256"],
            }
            result = consumer.evaluate_profile(embedded, report, trusted, "N1")
            self.assertEqual("INCOMPLETE", result["status"], result)
            self.assertIn("invalid:registration_source_binding_contains_self_hash", result["incomplete_reasons"])
            self.assertIn(
                "invalid:registration_embeds_final_source_binding",
                consumer.validate_registration(embedded),
            )

        with self.subTest("missing nested registration maps report a problem"):
            malformed = deepcopy(REGISTRATION)
            del malformed["workload_contract"]["profiles"]["N5"]["orders"]
            problems = consumer.validate_registration(malformed)
            self.assertIn("missing:N5_group_rpc_registration", problems)

        with self.subTest("NaN performance value is incomplete"):
            registration, trusted, report = _valid_n1_report()
            report["registered_performance_metrics"]["system_cpu"]["mean_percent"] = float("nan")
            result = consumer.evaluate_profile(registration, report, trusted, "N1")
            self.assertEqual("INCOMPLETE", result["status"], result)
            self.assertIn("system_cpu_summary_missing", result["incomplete_reasons"])

        profile, report = _valid_n5_group_projection()
        metrics = report["accounts_and_execution"]["n5_group_metrics"]
        self.assertEqual([], consumer.validate_registration(REGISTRATION))
        baseline_incomplete: list[str] = []
        baseline_failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, baseline_incomplete, baseline_failures)
        self.assertEqual([], baseline_incomplete)
        self.assertEqual([], baseline_failures)
        self.assertEqual(540, len(set().union(*(
            set(metrics["by_strategy"]["strategy-000"]["group_child_routes_by_leg"][leg]["intent_ids"])
            for leg in ("leg-put", "leg-future", "leg-call")
        ))))

        with self.subTest("missing physical provider evidence is incomplete despite derived intents"):
            missing_report = deepcopy(report)
            del missing_report["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]
            incomplete: list[str] = []
            failures: list[str] = []
            consumer._check_n5_group(REGISTRATION, profile, missing_report, incomplete, failures)
            self.assertIn("n5_group_provider_ledger_missing:account-00", incomplete)
            self.assertEqual([], failures)

        with self.subTest("missing exact GroupProofJournal to provider join is incomplete"):
            missing_join_report = deepcopy(report)
            del missing_join_report["accounts_and_execution"]["n5_group_metrics"]["intent_order_join_by_account"]["account-00"]
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, missing_join_report, incomplete, failures)
            self.assertIn("n5_group_intent_order_join_rows_missing:account-00", incomplete)

        with self.subTest("derived intents cannot hide a missing provider row in the exact join"):
            missing_join_write_report = deepcopy(report)
            join_row = missing_join_write_report["accounts_and_execution"]["n5_group_metrics"]["intent_order_join_by_account"]["account-00"][0]
            join_row["provider_write_count"] = 0
            join_row["provider_order_row_present"] = False
            join_row["join_state"] = "missing_provider_write"
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, missing_join_write_report, incomplete, failures)
            self.assertIn("n5_group_intent_order_join_write_count_mismatch:account-00:0", failures)
            self.assertIn("n5_group_intent_order_join_provider_row_missing:account-00:0", failures)
            self.assertIn("n5_group_intent_order_join_state_not_joined:account-00:0", failures)

        with self.subTest("provider ledger anomalies fail despite correct derived and joined intents"):
            orphan_report = deepcopy(report)
            ledger = orphan_report["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]
            ledger["write_without_order_record_count"] = 1
            ledger["unjoined_make_order_write_count"] = 1
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, orphan_report, incomplete, failures)
            self.assertIn("n5_group_provider_ledger_anomaly_nonzero:account-00:write_without_order_record_count", failures)

    def test_n5_provider_fill_quantity_and_readback_gates_fail_closed(self) -> None:
        profile, report = _valid_n5_group_projection()
        incomplete: list[str] = []
        failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertEqual([], incomplete)
        self.assertEqual([], failures)

        with self.subTest("filled status with zero filled quantity fails"):
            zero_filled = deepcopy(report)
            row = zero_filled["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]["writes_by_client_order_id"][0]
            row["order_status"] = "filled"
            row["filled"] = "0"
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, zero_filled, incomplete, failures)
            self.assertIn("n5_group_provider_write_row_filled_nonpositive:account-00", failures)

        with self.subTest("finite wrong leg quantity fails"):
            wrong_quantity = deepcopy(report)
            row = wrong_quantity["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]["writes_by_client_order_id"][0]
            row["quantity"] = "3"
            row["filled"] = "3"
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, wrong_quantity, incomplete, failures)
            self.assertIn("n5_group_provider_write_row_quantity_mismatch:account-00", failures)
            self.assertIn("n5_group_provider_write_row_filled_mismatch:account-00", failures)

        with self.subTest("zero fill query fails"):
            no_fill_query = deepcopy(report)
            join_row = no_fill_query["accounts_and_execution"]["n5_group_metrics"]["intent_order_join_by_account"]["account-00"][0]
            join_row["order_query_count"] = 0
            join_row["fill_query_count"] = 0
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, no_fill_query, incomplete, failures)
            self.assertIn("n5_group_intent_order_join_query_count_below_minimum:account-00:0:order_query_count", failures)
            self.assertIn("n5_group_intent_order_join_query_count_below_minimum:account-00:0:fill_query_count", failures)

        with self.subTest("nonfinite quantities are incomplete"):
            nonfinite = deepcopy(report)
            row = nonfinite["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]["writes_by_client_order_id"][0]
            row["filled"] = float("nan")
            row["quantity"] = float("inf")
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, nonfinite, incomplete, failures)
            self.assertIn("n5_group_provider_write_row_filled_invalid:account-00", incomplete)
            self.assertIn("n5_group_provider_write_row_quantity_invalid:account-00", incomplete)

        with self.subTest("missing provider quantities are incomplete"):
            missing = deepcopy(report)
            row = missing["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]["writes_by_client_order_id"][0]
            del row["quantity"]
            del row["filled"]
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, missing, incomplete, failures)
            self.assertIn("n5_group_provider_write_row_quantity_invalid:account-00", incomplete)
            self.assertIn("n5_group_provider_write_row_filled_invalid:account-00", incomplete)

        with self.subTest("missing actual group writes fail despite correct derived intents"):
            missing_write_report = deepcopy(report)
            ledger = missing_write_report["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]
            ledger["make_order_write_count"] = 0
            ledger["unique_client_order_id_count"] = 0
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, missing_write_report, incomplete, failures)
            self.assertIn("n5_group_provider_write_count_mismatch:account-00", failures)

        with self.subTest("duplicate provider client order id fails despite correct derived intents"):
            duplicate_report = deepcopy(report)
            ledger = duplicate_report["accounts_and_execution"]["n5_group_metrics"]["provider_ledger_by_account"]["account-00"]
            ledger["unique_client_order_id_count"] = 539
            ledger["duplicate_client_order_id_count"] = 1
            incomplete = []
            failures = []
            consumer._check_n5_group(REGISTRATION, profile, duplicate_report, incomplete, failures)
            self.assertIn("n5_group_provider_duplicate_client_order_id:account-00", failures)
            self.assertIn("n5_group_provider_unique_client_order_id_count_mismatch:account-00", failures)


    def test_n5_source_identity_map_preserves_aliases_and_rejects_source_aliases(self) -> None:
        profile, report = _valid_n5_group_projection()
        contract = REGISTRATION["workload_contract"]["profiles"]["N5"]["orders"]["n5_group_contract"]
        self.assertEqual([], consumer.validate_registration(REGISTRATION))

        incomplete: list[str] = []
        failures: list[str] = []
        consumer._check_n5_group(REGISTRATION, profile, report, incomplete, failures)
        self.assertEqual([], incomplete)
        self.assertEqual([], failures)
        aggregate = report["accounts_and_execution"]["n5_group_metrics"]
        source_identity = contract["source_identity_by_report_account"]["account-00"]
        self.assertIn("account-00", aggregate["provider_ledger_by_account"])
        self.assertEqual(
            source_identity["account_ref"],
            aggregate["provider_ledger_by_account"]["account-00"]["account_ref"],
        )
        self.assertEqual(
            source_identity["group_strategy_ref"],
            aggregate["provider_ledger_by_account"]["account-00"]["strategy_ref"],
        )

        bad_route_report = deepcopy(report)
        bad_route = bad_route_report["accounts_and_execution"]["n5_group_metrics"][
            "by_strategy"
        ]["strategy-000"]["group_child_routes_by_leg"]["leg-put"]
        bad_route["account_ref"] = "account-00"
        incomplete = []
        failures = []
        consumer._check_n5_group(REGISTRATION, profile, bad_route_report, incomplete, failures)
        self.assertIn("n5_group_child_route_owner_mismatch:0:leg-put", failures)

        bad_ledger_report = deepcopy(report)
        bad_ledger = bad_ledger_report["accounts_and_execution"]["n5_group_metrics"][
            "provider_ledger_by_account"
        ]["account-00"]
        bad_ledger["account_ref"] = "account-00"
        incomplete = []
        failures = []
        consumer._check_n5_group(REGISTRATION, profile, bad_ledger_report, incomplete, failures)
        self.assertIn(
            "n5_group_provider_ledger_identity_or_attribution_invalid:account-00",
            incomplete,
        )

        bad_registration = deepcopy(REGISTRATION)
        bad_registration["workload_contract"]["profiles"]["N5"]["orders"][
            "n5_group_contract"
        ]["source_identity_by_report_account"]["account-00"]["account_ref"] = "account-00"
        self.assertIn(
            "wrong:N5_group_source_identity_by_report_account",
            consumer.validate_registration(bad_registration),
        )



def _n6_query_report() -> dict:
    """Public producer shape, with synthetic timings; never an actual load receipt."""
    rows = []
    for index in range(2):
        denied = index == 1
        reply = {
            "request_sequence": index,
            "request_operation": "account.server_time_probe",
            **dict.fromkeys(("response_received", "reply_wire_envelope", "reply_kind_is_reply",
                            "correlation_matches", "operation_matches", "scope_matches",
                            "owner_epoch_matches", "peer_matches"), True),
            "reply_ok": not denied,
            "remote_error_code": "HANDLER_FAILED" if denied else "OK",
        }
        rows.append({
            "request_index": index, "outcome": "sdk_error" if denied else "returned",
            "observation_type": None if denied else "ExchangeServerTimeObservation",
            "error_code": "ACCOUNT_FACT_READ_FAILED" if denied else None,
            "elapsed_ns": 300_000_000 if denied else 1_000_000,
            "runtime_boundary": {
                "runtime_call_index": index, "mount": "store.account",
                "operation": "account.server_time_probe",
                "runtime_call_outcome": "raised" if denied else "returned",
                "runtime_error_type": "ServiceCallOutcomeUnknownError" if denied else None,
                "runtime_error_code": "BUSINESS_OUTCOME_UNKNOWN" if denied else None,
                "response_boundary": reply,
            },
        })
    return {"load": {"server_time_query_probe": {
        "status": "RUN_PENDING", "scenario": "n6_sdk_server_time_quota_probe",
        "quota_domain_ref": "synthetic:load01-n6-server-time-query-v1",
        "quota_capacity": 3, "reserved_capacity": 0, "quota_window_seconds": 3600.0,
        "quota_max_queue_wait_seconds": 0.25, "runtime_rpc_timeout_ms": 5000,
        "startup_slots_measured": {"login_dispatches": 1, "initial_account_read_dispatches": 1,
            "consumed_startup_slots": 2, "grants_at_owner_ready": 2, "reserved_capacity": 0},
        "strategy_queries": rows,
        "owner_typed_quota_denials": {"DEADLINE_EXCEEDED": 1},
        "correlated_remote_error_reply": {
            "server_reply_code": "HANDLER_FAILED", "runtime_error_code": "BUSINESS_OUTCOME_UNKNOWN",
            "sdk_error_code": "ACCOUNT_FACT_READ_FAILED", "request_sequence": 1,
            "owner_attempt_sequence": 2, "correlation_matches": True,
            "owner_attempt_sequence_matches": True, "provider_dispatch_count_remained_one": True,
        },
        "provider_server_time_dispatches": 1, "durable_grants_in_window": 3, "pending_waiters": 0,
        "subject_scope": "account_only; no strategy-fairness claim",
        "provider_boundary": "synthetic local provider; no exchange request was sent",
        "evidence_scope": "query-path source integration only",
        "trigger_elapsed_seconds": 60.01, "sdk_call_sequence_wall_time_ms": 302.0,
        "measurement_scope": "wall time around the two serial SDK calls; no separate quota queue-residence latency",
    }}}


class EngineeringRegistrationR2Tests(unittest.TestCase):
    def check_probe(self, report: dict) -> tuple[list[str], list[str]]:
        incomplete, failures = [], []
        consumer._check_n6_server_time_query(
            REGISTRATION["workload_contract"]["profiles"]["N6"], report, incomplete, failures
        )
        return incomplete, failures

    def test_producer_query_shape_is_accepted_without_claiming_full_load(self) -> None:
        self.assertEqual(([], []), self.check_probe(_n6_query_report()))
        result = consumer.evaluate_matrix(REGISTRATION, {}, None)
        self.assertEqual("NOT_CLAIMED_BY_CONSUMER", result["formal_acceptance_status"])
        self.assertEqual(["U0"], result["p0_baseline_profiles"])
        self.assertEqual("RUN_PENDING", REGISTRATION["workload_contract"]["profiles"]["N6"]
                         ["orders"]["n6_server_time_query_component"]["full_profile_execution_status"])

    def test_n6_profile_evaluation_requires_query_report(self) -> None:
        registration, trusted, report = _valid_n1_report()
        report["load_profile"] = "N6"
        result = consumer.evaluate_profile(registration, report, trusted, "N6")
        self.assertIn("missing:load.server_time_query_probe", result["incomplete_reasons"])

    def test_reply_and_typed_denial_boundaries_are_required(self) -> None:
        reply_fields = ("response_received", "reply_wire_envelope", "reply_kind_is_reply",
                        "correlation_matches", "operation_matches", "scope_matches",
                        "owner_epoch_matches", "peer_matches")
        for index in range(2):
            for field in reply_fields:
                with self.subTest(index=index, field=field):
                    report = _n6_query_report()
                    report["load"]["server_time_query_probe"]["strategy_queries"][index][
                        "runtime_boundary"]["response_boundary"][field] = False
                    self.assertIn(f"n6_server_time_query_correlated_reply_mismatch:{index}", self.check_probe(report)[1])
        mutations = [
            (("owner_typed_quota_denials", "DEADLINE_EXCEEDED"), 0),
            (("provider_server_time_dispatches",), 2),
            (("durable_grants_in_window",), 4),
            (("pending_waiters",), 1),
            (("correlated_remote_error_reply", "owner_attempt_sequence"), 3),
            (("correlated_remote_error_reply", "provider_dispatch_count_remained_one"), False),
            (("strategy_queries", 1, "runtime_boundary", "response_boundary", "remote_error_code"), "OK"),
            (("strategy_queries", 1, "runtime_boundary", "runtime_error_code"), "OTHER"),
            (("trigger_elapsed_seconds",), 59.99),
            (("strategy_queries", 1, "elapsed_ns"), 5_000_000_000),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                report = _n6_query_report()
                target = report["load"]["server_time_query_probe"]
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                self.assertTrue(any(self.check_probe(report)), path)

    def test_boolean_counts_and_indices_are_rejected(self) -> None:
        paths = [
            ("reserved_capacity",), ("startup_slots_measured", "login_dispatches"),
            ("startup_slots_measured", "reserved_capacity"), ("owner_typed_quota_denials", "DEADLINE_EXCEEDED"),
            ("strategy_queries", 0, "request_index"), ("strategy_queries", 1, "request_index"),
            ("strategy_queries", 1, "runtime_boundary", "runtime_call_index"),
            ("strategy_queries", 0, "runtime_boundary", "response_boundary", "request_sequence"),
            ("correlated_remote_error_reply", "request_sequence"),
            ("provider_server_time_dispatches",), ("pending_waiters",),
        ]
        for path in paths:
            with self.subTest(path=path):
                report = _n6_query_report()
                target = report["load"]["server_time_query_probe"]
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = bool(target[path[-1]])
                self.assertTrue(any(self.check_probe(report)), path)

    def test_registration_alias_mapping_and_query_operation_are_exact(self) -> None:
        self.assertEqual([], consumer.validate_registration(REGISTRATION))
        for mutation in ("missing", "extra", "wrong"):
            with self.subTest(mutation=mutation):
                registration = deepcopy(REGISTRATION)
                aliases = registration["matrix_consumer_contract"]["required_fixed_source_binding_aliases"]
                key = next(iter(aliases))
                if mutation == "missing":
                    aliases.pop(key)
                elif mutation == "extra":
                    aliases["unknown"] = "unknown"
                else:
                    aliases[key] = "wrong"
                self.assertIn("missing_or_invalid:required_fixed_source_binding_aliases",
                              consumer.validate_registration(registration))
        registration = deepcopy(REGISTRATION)
        registration["workload_contract"]["profiles"]["N6"]["orders"][
            "n6_server_time_query_component"]["provider_quota_operation"] = "CTP_QUERY"
        self.assertIn("wrong:N6_server_time_query_component_registration",
                      consumer.validate_registration(registration))

    def test_registration_rejects_integer_substitutes_for_boolean_flags(self) -> None:
        for index, field in ((0, "reply_ok"), (1, "reply_ok"),
                             (1, "correlated_remote_quota_denial"),
                             (1, "provider_dispatch_count_remained_one")):
            with self.subTest(index=index, field=field):
                registration = deepcopy(REGISTRATION)
                calls = registration["workload_contract"]["profiles"]["N6"]["orders"][
                    "n6_server_time_query_component"]["serial_calls"]
                calls[index][field] = int(calls[index][field])
                self.assertIn("wrong:N6_server_time_query_component_numeric_types",
                              consumer.validate_registration(registration))

    def test_admission_wave_retains_integer_counts_and_provider_separation(self) -> None:
        profile = REGISTRATION["workload_contract"]["profiles"]["N6"]
        report = {
            "load": {"quota_admission_wave": {
                "offered": 200, "granted": 100, "deadline_exceeded": 100,
                "provider_query_dispatched_by_wave": False, "pending_waiters_after_collection": 0,
                "grant_p95_ns": 20_000_000_000, "denial_p95_ns": 30_000_000_000,
            }},
            "strategy_results": {str(index): {"quota_admission_wave": {"outcomes": [
                {"strategy_index": index, "outcome": "GRANTED"},
                {"strategy_index": index, "outcome": "DEADLINE_EXCEEDED"},
            ]}} for index in range(100)},
        }
        def check(candidate):
            incomplete, failures = [], []
            consumer._check_n6_quota(profile, candidate, incomplete, failures)
            return incomplete, failures
        self.assertEqual(([], []), check(report))
        bad = deepcopy(report)
        bad["load"]["quota_admission_wave"]["pending_waiters_after_collection"] = False
        self.assertIn("n6_quota_pending_waiters_nonzero_or_missing", check(bad)[1])
        bad = deepcopy(report)
        bad["strategy_results"]["1"]["quota_admission_wave"]["outcomes"][0]["strategy_index"] = True
        self.assertIn("n6_quota_outcome_identity_invalid:1", check(bad)[0])
        bad = deepcopy(report)
        bad["load"]["quota_admission_wave"]["provider_query_dispatched_by_wave"] = True
        self.assertIn("n6_quota_provider_query_separation_failed", check(bad)[1])

    def test_user_engineering_approval_has_exact_limits(self) -> None:
        approval = REGISTRATION["user_approval"]
        self.assertEqual({"goodput_tolerance_fraction": 0.05, "per_rpc_timeout_seconds": 5,
                          "reconnect_recovery_seconds": 10,
                          "required_latency_percentiles": ["p50", "p95", "p99"]},
                         approval["approved_existing_thresholds"])
        for key in ("derived_profile_targets_individually_approved", "low_latency_or_hft_claimed",
                    "user_sla_claimed", "formal_load01_acceptance_claimed"):
            self.assertIs(approval[key], False)

    def test_runner_loader_reads_r2_and_enforces_byte_and_manifest_binding(self) -> None:
        runner = REPOSITORY_ROOT / "tests/unit/test_load01_hundred_strategy_30min.py"
        tree = ast.parse(runner.read_text(encoding="utf-8"), filename=str(runner))
        wanted = {"_LOAD01_THRESHOLD_REGISTRATION_PATH", "_LOAD01_THRESHOLD_REGISTRATION_SHA256"}
        selected = [node for node in tree.body if
                    (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in wanted for t in node.targets))
                    or (isinstance(node, ast.FunctionDef) and node.name == "_load01_read_threshold_registration")]
        self.assertEqual(3, len(selected))
        namespace = {"Path": Path, "Mapping": Mapping, "hashlib": hashlib, "json": json}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(runner), "exec"), namespace)
        relative = namespace["_LOAD01_THRESHOLD_REGISTRATION_PATH"]
        self.assertEqual("docs/evidence/iteration10-load01-engineering-registration-20261018/threshold-registration-r2.json", relative)
        pin = namespace["_LOAD01_THRESHOLD_REGISTRATION_SHA256"]
        read = namespace["_load01_read_threshold_registration"]
        registration, receipt = read(REPOSITORY_ROOT, {relative: pin})
        self.assertEqual(REGISTRATION, registration)
        self.assertEqual(pin, receipt["sha256"])
        with self.assertRaisesRegex(AssertionError, "absent from the fixed-source manifest"):
            read(REPOSITORY_ROOT, {})
        with tempfile.TemporaryDirectory(prefix="load01-registration-") as folder:
            root = Path(folder)
            target = root / relative
            target.parent.mkdir(parents=True)
            target.write_bytes((REPOSITORY_ROOT / relative).read_bytes() + b" ")
            with self.assertRaisesRegex(AssertionError, "bytes differ"):
                read(root, {relative: pin})


if __name__ == "__main__":
    unittest.main(verbosity=2)
