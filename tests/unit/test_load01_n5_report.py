from __future__ import annotations

import unittest

from tests.unit._load01_n5_report import build_n5_group_report
from tests.unit._load01_profiles import load01_profile


class _Profile:
    profile_id = "N5"
    strategy_indices_by_account = ((0, 1), (2, 3))
    expected_group_capable_strategies = (0, 2)


def _strategy_metrics(*, groups: int, children: int) -> dict[str, object]:
    return {
        "expected_logical_group_count": groups,
        "observed_logical_group_count": groups,
        "expected_child_dispatch_count": children,
        "observed_child_dispatch_count": children,
        "expected": {
            "groups.submit": groups,
            "groups.query": groups,
            "group_logical_count": groups,
            "group_child_dispatch_count": children,
        },
        "observed": {
            "group_logical_count": groups,
            "group_success_count": groups,
            "group_failure_count": 0,
            "group_unresolved_count": 0,
            "group_child_dispatch_count": children,
        },
    }


def _owner(account_index: int) -> tuple[dict[str, object], dict[str, object]]:
    account_ref = f"gw02-account-{account_index:02d}"
    strategy_ref = f"load01-strategy-{account_index * 2:03d}"
    ledger: dict[str, object] = {
        "attribution": "n5_group_proof_runtime_only",
        "account_ref": account_ref,
        "strategy_ref": strategy_ref,
        "make_order_write_count": 1,
    }
    join_rows: list[dict[str, object]] = [
        {
            "group_id": f"group-{account_index}",
            "leg_id": "leg-put",
            "intent_id": f"intent-{account_index}",
            "client_order_id": f"provider-{account_index}",
            "join_state": "joined",
        }
    ]
    reservations: list[dict[str, object]] = [
        {"group_id": f"group-{account_index}", "leg_id": "leg-put", "state": "RESOLVED"}
    ]
    runtime: dict[str, object] = {
        "account_ref": account_ref,
        "strategy_ref": strategy_ref,
        "provider_ledger": ledger,
        "intent_order_join": join_rows,
        "intent_order_join_count": 1,
        "joined_intent_order_count": 1,
        "risk_reservations": reservations,
    }
    return (
        {
            "account_ref": account_ref,
            "n5_group_runtime": runtime,
        },
        runtime,
    )


class N5ReportAggregationTests(unittest.TestCase):
    def test_mixed_strategy_counts_preserve_owner_join_and_ledger_facts(self) -> None:
        group_0 = _strategy_metrics(groups=3, children=9)
        ordinary_1 = _strategy_metrics(groups=0, children=0)
        group_2 = _strategy_metrics(groups=2, children=6)
        ordinary_3 = _strategy_metrics(groups=0, children=0)
        strategy_results = {
            0: {"n5_group_metrics": group_0},
            1: {"n5_group_metrics": ordinary_1},
            2: {"n5_group_metrics": group_2},
            3: {"n5_group_metrics": ordinary_3},
        }
        owner_0, runtime_0 = _owner(0)
        owner_1, runtime_1 = _owner(1)
        owner_totals = {0: owner_0, 1: owner_1}

        report = build_n5_group_report(
            _Profile(),
            strategy_results,
            owner_totals,
            duration_seconds=1800,
            actual_duration=1799.5,
        )

        self.assertEqual(report["status"], "SOURCE_FACTS_COMPLETE")
        self.assertEqual(report["formal_acceptance_status"], "NOT_CLAIMED_BY_REPORT_AGGREGATOR")
        self.assertEqual(report["planned_duration_seconds"], 1800)
        self.assertEqual(report["actual_duration_seconds"], 1799.5)
        self.assertIs(report["by_strategy"]["strategy-000"], group_0)
        self.assertIs(report["by_strategy"]["strategy-001"], ordinary_1)
        self.assertEqual(report["summary"]["strategy_count"], 4)
        self.assertEqual(report["summary"]["group_capable_strategy_count"], 2)
        self.assertEqual(report["summary"]["ordinary_strategy_count"], 2)
        self.assertEqual(
            report["summary"]["totals"],
            {
                "expected_logical_group_count": 5,
                "observed_logical_group_count": 5,
                "expected_child_dispatch_count": 15,
                "observed_child_dispatch_count": 15,
                "observed_group_failure_count": 0,
                "observed_group_unresolved_count": 0,
            },
        )
        self.assertEqual(
            report["summary"]["by_account"]["account-00"]["observed_child_dispatch_count"],
            9,
        )
        self.assertEqual(
            report["summary"]["by_account"]["account-01"]["observed_child_dispatch_count"],
            6,
        )
        self.assertIs(report["provider_ledger_by_account"]["account-00"], runtime_0["provider_ledger"])
        self.assertIs(report["intent_order_join_by_account"]["account-00"], runtime_0["intent_order_join"])
        self.assertIs(report["risk_reservations_by_account"]["account-01"], runtime_1["risk_reservations"])
        self.assertEqual(
            report["owner_join_counts_by_account"]["account-01"],
            {
                "intent_order_join_count": 1,
                "joined_intent_order_count": 1,
            },
        )

        # Missing owner facts stay missing and make the report incomplete; no
        # empty ledger/join or zero observed counters are invented.
        missing_owner = build_n5_group_report(
            _Profile(),
            strategy_results,
            {0: owner_0},
            duration_seconds=1800,
            actual_duration=1799.5,
        )
        self.assertEqual(missing_owner["status"], "INCOMPLETE")
        self.assertIn("owner_total_missing:1", missing_owner["incomplete_reasons"])
        self.assertNotIn("account-01", missing_owner["provider_ledger_by_account"])
        self.assertNotIn("account-01", missing_owner["intent_order_join_by_account"])

    def test_runner_shaped_ten_strategy_source_identities_keep_report_aliases(self) -> None:
        profile = load01_profile("N5")
        strategy_count = len(profile.accounts_by_strategy)
        account_count = len(profile.strategy_indices_by_account)
        self.assertEqual(
            profile.strategy_indices_by_account,
            (tuple(range(5)), tuple(range(5, 10))),
        )
        self.assertEqual(profile.expected_group_capable_strategies, (0, 5))

        strategy_results: dict[int, dict[str, object]] = {}
        for index in range(strategy_count):
            is_group = index in profile.expected_group_capable_strategies
            strategy_results[index] = {
                "n5_group_metrics": _strategy_metrics(
                    groups=2 if is_group else 0,
                    children=6 if is_group else 0,
                )
            }

        owner_totals: dict[int, dict[str, object]] = {}
        runtimes: dict[int, dict[str, object]] = {}
        for account_index in range(account_count):
            source_strategy_index = profile.strategy_indices_by_account[account_index][0]
            source_account_ref = f"gw02-account-{account_index:02d}"
            source_strategy_ref = f"load01-strategy-{source_strategy_index:03d}"
            ledger: dict[str, object] = {
                "attribution": "n5_group_proof_runtime_only",
                "account_ref": source_account_ref,
                "strategy_ref": source_strategy_ref,
                "make_order_write_count": 6,
            }
            join_rows: list[dict[str, object]] = [
                {
                    "group_id": f"group-{account_index}-put",
                    "leg_id": "put",
                    "intent_id": f"intent-{account_index}-put",
                    "client_order_id": f"provider-{account_index}-put",
                    "join_state": "joined",
                }
            ]
            reservations: dict[str, object] = {
                "group_count": 2,
                "groups": [
                    {
                        "group_id": f"group-{account_index}-put",
                        "legs": [
                            {
                                "leg_id": "put",
                                "intent_id": f"intent-{account_index}-put",
                                "state": "RESOLVED",
                            }
                        ],
                    }
                ],
            }
            runtime: dict[str, object] = {
                "account_ref": source_account_ref,
                "strategy_ref": source_strategy_ref,
                "provider_ledger": ledger,
                "intent_order_join": join_rows,
                "intent_order_join_count": 6,
                "joined_intent_order_count": 6,
                "risk_reservations": reservations,
            }
            runtimes[account_index] = runtime
            owner_totals[account_index] = {
                "account_ref": source_account_ref,
                "n5_group_runtime": runtime,
            }

        report = build_n5_group_report(
            profile,
            strategy_results,
            owner_totals,
            duration_seconds=1800,
            actual_duration=1799.5,
        )

        self.assertEqual(report["status"], "SOURCE_FACTS_COMPLETE")
        self.assertEqual(report["summary"]["strategy_count"], 10)
        self.assertEqual(report["summary"]["ordinary_strategy_count"], 8)
        self.assertEqual(report["summary"]["totals"]["observed_logical_group_count"], 4)
        self.assertEqual(report["summary"]["totals"]["observed_child_dispatch_count"], 12)
        self.assertEqual(set(report["provider_ledger_by_account"]), {
            f"account-{account_index:02d}" for account_index in range(account_count)
        })
        self.assertEqual(
            report["provider_ledger_by_account"]["account-01"]["account_ref"],
            "gw02-account-01",
        )
        self.assertEqual(
            report["by_strategy"]["strategy-005"]["observed_child_dispatch_count"],
            6,
        )
        self.assertIs(report["provider_ledger_by_account"]["account-01"],
                      runtimes[1]["provider_ledger"])
        self.assertIs(report["intent_order_join_by_account"]["account-01"],
                      runtimes[1]["intent_order_join"])
        self.assertIs(report["risk_reservations_by_account"]["account-01"],
                      runtimes[1]["risk_reservations"])

        # A stale local alias in a provider fact must not be mistaken for the
        # actual runner identity, even though report map keys stay aliased.
        bad_owner_totals = dict(owner_totals)
        bad_runtime = dict(runtimes[1])
        bad_ledger = dict(runtimes[1]["provider_ledger"])
        bad_ledger["account_ref"] = "account-01"
        bad_runtime["provider_ledger"] = bad_ledger
        bad_owner_totals[1] = {
            "account_ref": "gw02-account-01",
            "n5_group_runtime": bad_runtime,
        }
        mismatched = build_n5_group_report(
            profile,
            strategy_results,
            bad_owner_totals,
            duration_seconds=1800,
            actual_duration=1799.5,
        )
        self.assertEqual(mismatched["status"], "INCOMPLETE")
        self.assertIn(
            "owner_provider_ledger_account_ref_mismatch:1",
            mismatched["incomplete_reasons"],
        )
        self.assertIs(mismatched["provider_ledger_by_account"]["account-01"], bad_ledger)


if __name__ == "__main__":
    unittest.main()
