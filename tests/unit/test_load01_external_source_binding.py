from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from scripts import load01_matrix_consumer as consumer


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = json.loads(
    (
        REPOSITORY_ROOT
        / "docs"
        / "evidence"
        / "iteration10-load01-engineering-registration-20261018"
        / "threshold-registration.json"
    ).read_text(encoding="utf-8")
)


def _valid_binding_case() -> tuple[dict, dict, dict]:
    # Keep this older node on the same production-shaped runner-key map as the
    # focused complete-binding test below. Semantic aliases live only in
    # trusted source_pins; report source_bindings use actual module/test keys.
    return _valid_runner_key_binding_case()


def _valid_runner_key_binding_case() -> tuple[dict, dict, dict]:
    registration = deepcopy(REGISTRATION)
    aliases = registration["source_scope"]["required_runtime_source_aliases"]
    report_source_bindings = {}
    source_pins = {}
    for index, alias in enumerate(sorted(consumer.SOURCE_ALIASES), start=1):
        report_key = consumer.SOURCE_ALIAS_REPORT_KEYS[alias]
        path = aliases[alias]["path"]
        digest = f"{index:064x}"
        report_source_bindings[report_key] = {
            "path": path,
            "sha256": digest,
            "bytes": index,
        }
        source_pins[alias] = dict(report_source_bindings[report_key])
    # The runner binds every imported module and focused test, not only the
    # semantic aliases consumed by the registration contract.
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

    registration_sha256 = "a" * 64
    sdk_head = "b" * 40
    trusted = {
        "status": "BOUND",
        "sdk_head": sdk_head,
        "registration_sha256": registration_sha256,
        "source_pins": source_pins,
        "report_source_bindings": deepcopy(report_source_bindings),
    }
    report = {
        "fixed_parent_commit": sdk_head,
        "source_bindings": deepcopy(report_source_bindings),
        "registered_performance_metrics": {
            "registration": {"sha256": registration_sha256}
        },
    }
    return registration, report, trusted


class ExternalSourceBindingReceiptTests(unittest.TestCase):
    def test_root_receipt_maps_semantic_aliases_to_complete_runner_binding_keys(self) -> None:
        registration, report, trusted = _valid_runner_key_binding_case()
        self.assertEqual([], consumer.validate_registration(registration))
        self.assertEqual([], consumer._source_binding_issues(registration, report, trusted))
        self.assertIn("n5_report", consumer.SOURCE_ALIASES)
        self.assertIn(
            consumer.SOURCE_ALIAS_REPORT_KEYS["n5_report"], report["source_bindings"]
        )
        self.assertGreater(len(report["source_bindings"]), len(consumer.SOURCE_ALIASES))

        with self.subTest("all extra runner records must match the Root receipt"):
            changed_extra = deepcopy(report)
            changed_extra["source_bindings"]["tests.integration._dim09_three_leg_fixtures"][
                "sha256"
            ] = "f" * 64
            self.assertIn(
                "incomplete:report_source_bindings_mismatch",
                consumer._source_binding_issues(registration, changed_extra, trusted),
            )

        with self.subTest("required N5 report helper must be bound"):
            missing_helper = deepcopy(report)
            key = consumer.SOURCE_ALIAS_REPORT_KEYS["n5_report"]
            missing_helper["source_bindings"].pop(key)
            self.assertIn(
                "incomplete:report_source_bindings_mismatch",
                consumer._source_binding_issues(registration, missing_helper, trusted),
            )
            self.assertIn(
                "incomplete:trusted_report_required_source_keys_missing",
                consumer._source_binding_issues(
                    registration,
                    missing_helper,
                    {**trusted, "report_source_bindings": missing_helper["source_bindings"]},
                ),
            )

        with self.subTest("source pins remain semantic alias keyed"):
            missing_pin = deepcopy(trusted)
            missing_pin["source_pins"].pop("n5_report")
            self.assertIn(
                "incomplete:trusted_source_alias_set_mismatch",
                consumer._source_binding_issues(registration, report, missing_pin),
            )

    def test_root_receipt_binds_raw_registration_head_and_every_report_alias(self) -> None:
        registration, report, trusted = _valid_binding_case()
        self.assertEqual([], consumer.validate_registration(registration))
        self.assertEqual([], consumer._source_binding_issues(registration, report, trusted))

        with self.subTest("report cannot supply its own receipt"):
            report_with_spoof = deepcopy(report)
            report_with_spoof["trusted_binding"] = deepcopy(trusted)
            self.assertIn(
                "missing:external_trusted_source_binding",
                consumer._source_binding_issues(registration, report_with_spoof, None),
            )

        with self.subTest("report fixed parent must match the external SDK head"):
            mismatched = deepcopy(report)
            mismatched["fixed_parent_commit"] = "c" * 40
            self.assertIn(
                "incomplete:report_head_binding_mismatch",
                consumer._source_binding_issues(registration, mismatched, trusted),
            )

        with self.subTest("report registration pin must match raw registration receipt"):
            mismatched = deepcopy(report)
            mismatched["registered_performance_metrics"]["registration"]["sha256"] = "d" * 64
            self.assertIn(
                "incomplete:report_registration_pin_mismatch",
                consumer._source_binding_issues(registration, mismatched, trusted),
            )

        with self.subTest("receipt must be Root-bound"):
            unbound = deepcopy(trusted)
            unbound["status"] = "PENDING"
            self.assertIn(
                "incomplete:external_trusted_source_binding_not_bound",
                consumer._source_binding_issues(registration, report, unbound),
            )

        for alias in sorted(consumer.SOURCE_ALIASES):
            report_key = consumer.SOURCE_ALIAS_REPORT_KEYS[alias]
            with self.subTest(alias=alias, field="path"):
                tampered = deepcopy(report)
                tampered["source_bindings"][report_key]["path"] = "tests/unit/unbound.py"
                issues = consumer._source_binding_issues(registration, tampered, trusted)
                self.assertIn(f"incomplete:report_source_bindings_mismatch", issues)
                self.assertIn(f"incomplete:report_source_path_mismatch:{alias}", issues)

            with self.subTest(alias=alias, field="sha256"):
                tampered = deepcopy(report)
                tampered["source_bindings"][report_key]["sha256"] = "e" * 64
                issues = consumer._source_binding_issues(registration, tampered, trusted)
                self.assertIn("incomplete:report_source_bindings_mismatch", issues)
                self.assertIn(f"incomplete:report_source_sha256_mismatch:{alias}", issues)

        with self.subTest("trusted pins must cover exactly the registered aliases"):
            incomplete = deepcopy(trusted)
            incomplete["source_pins"].pop(sorted(consumer.SOURCE_ALIASES)[0])
            self.assertIn(
                "incomplete:trusted_source_alias_set_mismatch",
                consumer._source_binding_issues(registration, report, incomplete),
            )

        with self.subTest("receipt contract rejects report-derived authority"):
            invalid_registration = deepcopy(registration)
            invalid_registration["source_scope"]["external_trusted_binding_requirements"][
                "report_may_supply_receipt"
            ] = True
            self.assertIn(
                "incomplete:external_trusted_binding_requirements",
                consumer._source_binding_issues(invalid_registration, report, trusted),
            )


if __name__ == "__main__":
    unittest.main()
