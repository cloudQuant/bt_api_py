"""The quality ratchet keeps lint debt from creeping back into the submodules.

These tests are offline: they exercise the pure counting/comparison helpers and
the scope wiring, not a live `ruff` run over the whole repository.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import check_quality_ratchet as ratchet

ROOT = Path(__file__).resolve().parents[2]


class TestCounting:
    def test_counts_findings_per_rule(self) -> None:
        payload = [
            {"code": "F401", "filename": "a.py"},
            {"code": "F401", "filename": "b.py"},
            {"code": "W291", "filename": "a.py"},
        ]

        assert ratchet.counts_from_ruff_payload(payload) == {"F401": 2, "W291": 1}

    def test_missing_code_is_counted_as_unknown(self) -> None:
        """Syntax errors have no rule code; they must not vanish from the total."""
        assert ratchet.counts_from_ruff_payload([{"code": None}]) == {"unknown": 1}

    def test_empty_payload(self) -> None:
        assert ratchet.counts_from_ruff_payload([]) == {}


class TestComparison:
    def test_higher_count_is_a_regression(self) -> None:
        comparison = ratchet.compare_counts({"F401": 5}, {"F401": 4})

        assert comparison.ok is False
        assert comparison.regressions == ["F401: 5 > baseline 4 (+1)"]

    def test_new_rule_is_a_regression(self) -> None:
        comparison = ratchet.compare_counts({"S603": 1}, {})

        assert comparison.ok is False
        assert "S603" in comparison.regressions[0]

    def test_lower_count_is_an_improvement(self) -> None:
        comparison = ratchet.compare_counts({"W291": 3}, {"W291": 10})

        assert comparison.ok is True
        assert comparison.improvements == ["W291: 3 < baseline 10 (-7)"]

    def test_equal_counts_are_recorded_as_unchanged(self) -> None:
        comparison = ratchet.compare_counts({"F821": 135}, {"F821": 135})

        assert comparison.ok is True
        assert comparison.unchanged == ["F821: 135"]

    def test_a_cleared_rule_is_not_reported_as_unchanged(self) -> None:
        comparison = ratchet.compare_counts({}, {"F401": 3})

        assert comparison.improvements == ["F401: 0 < baseline 3 (-3)"]
        assert comparison.unchanged == []


class TestScopeGuard:
    def test_missing_snapshot_path_is_detected(self) -> None:
        """A checkout without submodules scans less and would pass silently."""
        missing = ratchet.missing_scope_paths(
            ["bt_api_py", "tests"], ["bt_api_py", "tests", "bt_api/bt_api_okx/src"]
        )

        assert missing == ["bt_api/bt_api_okx/src"]

    def test_trailing_slash_is_tolerated(self) -> None:
        assert (
            ratchet.missing_scope_paths(["bt_api/bt_api_okx/src/"], ["bt_api/bt_api_okx/src"]) == []
        )

    def test_complete_scope_has_no_missing_paths(self) -> None:
        scope = ratchet.default_scope()

        assert ratchet.missing_scope_paths(scope, scope) == []


class TestDefaultScope:
    def test_covers_main_package_tests_tooling_and_examples(self) -> None:
        scope = ratchet.default_scope()

        for path in ("bt_api_py", "tests", "scripts", "examples"):
            assert path in scope

    def test_covers_every_submodule_source_and_tests(self) -> None:
        scope = set(ratchet.default_scope())

        for pattern in ("bt_api/bt_api_*/src", "bt_api/bt_api_*/tests"):
            for path in ROOT.glob(pattern):
                assert str(path.relative_to(ROOT)) in scope, f"{path} is not gated"

    def test_print_scope_cli_matches_the_python_api(self, capsys) -> None:
        assert ratchet.main(["--print-scope"]) == 0

        printed = capsys.readouterr().out.split()

        assert printed == ratchet.default_scope()


class TestMakefileParity:
    """The Makefile mirrors the script's scope; drift would silently un-gate paths."""

    def test_lint_all_matches_the_script_scope(self) -> None:
        make = shutil.which("make")
        if make is None:
            pytest.skip("make is not available")

        result = subprocess.run(
            [make, "-n", "lint-all"], cwd=ROOT, capture_output=True, text=True, check=False
        )

        assert result.returncode == 0, result.stderr
        command = next(
            line for line in result.stdout.splitlines() if line.strip().startswith("ruff check")
        )
        make_scope = command.split()[2:]

        assert make_scope == ratchet.default_scope()


class TestCommittedSnapshot:
    def test_snapshot_is_present_and_well_formed(self) -> None:
        payload = json.loads(ratchet.DEFAULT_BASELINE.read_text(encoding="utf-8"))

        assert payload["schema_version"] == ratchet.SCHEMA_VERSION
        assert payload["ruff_version"].startswith("ruff ")
        assert payload["scope"], "the snapshot must record the gated paths"
        assert payload["ruff"]["by_rule"], "the snapshot must record per-rule counts"
        assert payload["ruff"]["total"] == sum(payload["ruff"]["by_rule"].values())

    def test_snapshot_scope_matches_the_current_checkout(self) -> None:
        """Guards against the snapshot drifting away from what we actually gate."""
        payload = json.loads(ratchet.DEFAULT_BASELINE.read_text(encoding="utf-8"))

        assert ratchet.missing_scope_paths(ratchet.default_scope(), payload["scope"]) == []

    def test_ruff_version_is_reported_for_reproducibility(self) -> None:
        probe = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True, check=False
        )
        if probe.returncode != 0:
            pytest.skip("ruff is not installed")

        assert ratchet.ruff_version().startswith("ruff ")
