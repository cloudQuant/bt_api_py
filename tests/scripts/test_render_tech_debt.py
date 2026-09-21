"""Behavior contracts for the read-only Ruff ignore debt report."""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts.ci import render_tech_debt as report

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_COUNTS = {
    "TC001": 22,
    "TC003": 15,
}
EXPECTED_REASONS = {
    "TC001": "deferred annotations make many runtime imports appear type-only",
    "TC003": "gradual cleanup for stdlib type-only imports",
}


class TestRuleConfiguration:
    def test_rule_comments_are_loaded_from_the_root_ruff_ignore_list(self):
        reasons = report.load_rule_reasons()

        assert reasons == EXPECTED_REASONS

    def test_zero_count_rules_are_not_ignored_for_root_or_tests(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        ignored_rules = config["tool"]["ruff"]["lint"]["ignore"]
        per_file_ignores = config["tool"]["ruff"]["lint"]["per-file-ignores"]
        test_ignored_rules = per_file_ignores["tests/*.py"]
        ctp_ignored_rules = per_file_ignores["bt_api_py/ctp/*.py"]

        for rule in ("S110", "S112", "S113", "PERF401", "PERF402", "PERF403"):
            assert rule not in ignored_rules
            assert rule not in test_ignored_rules
        assert "S110" in ctp_ignored_rules
        assert "TC003" in ignored_rules
        assert "TC003" not in test_ignored_rules

    def test_missing_required_ignore_rule_fails_closed(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.ruff.lint]\nignore = [\n  "TC003", # gradual type imports\n]\n',
            encoding="utf-8",
        )

        with pytest.raises(report.TechDebtReportError, match="TC001"):
            report.load_rule_reasons(pyproject)


class TestRuffJson:
    def test_parses_selected_rules_to_expected_counts(self):
        findings = [{"code": rule} for rule, count in EXPECTED_COUNTS.items() for _ in range(count)]

        counts = report.parse_ruff_json(json.dumps(findings), returncode=1)

        assert counts == EXPECTED_COUNTS
        assert sum(counts.values()) == 37

    def test_initializes_zero_counts_for_unreported_selected_rules(self):
        counts = report.parse_ruff_json('[{"code": "TC001"}]', returncode=1)

        assert counts == {"TC001": 1, "TC003": 0}

    @pytest.mark.parametrize(
        ("output", "returncode"),
        [
            ("not json", 0),
            ('{"code": "TC001"}', 1),
            ("[]", 2),
            ("[]", 1),
            ('[{"code": "TC001"}]', 0),
        ],
    )
    def test_invalid_json_or_ruff_exit_state_fails_closed(self, output, returncode):
        with pytest.raises(report.TechDebtReportError):
            report.parse_ruff_json(output, returncode)

    def test_unexpected_rule_code_fails_closed(self):
        with pytest.raises(report.TechDebtReportError, match="unexpected Ruff rule"):
            report.parse_ruff_json('[{"code": "E501"}]', returncode=1)


class TestMarkdownReport:
    def test_report_includes_all_selected_rules_reasons_and_total(self):
        markdown = report.render_report(EXPECTED_COUNTS, EXPECTED_REASONS)

        assert "Scope: `bt_api_py`, `tests`, `scripts`, `examples`" in markdown
        assert "Submodules are not scanned" in markdown
        for rule, count in EXPECTED_COUNTS.items():
            assert f"`{rule}` | {count} | {EXPECTED_REASONS[rule]} |" in markdown
        assert "| **Total** | **37** |" in markdown


class TestFixedRuffInvocation:
    def test_invocation_uses_fixed_argv_root_cwd_and_no_shell(self, monkeypatch):
        calls = []
        completed = subprocess.CompletedProcess([], 0, "[]", "")

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return completed

        monkeypatch.setattr(report.subprocess, "run", fake_run)

        assert report.run_ruff() is completed
        assert calls == [
            (
                [
                    report.sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "--output-format",
                    "json",
                    "--select",
                    ",".join(report.RULES),
                    "bt_api_py",
                    "tests",
                    "scripts",
                    "examples",
                ],
                {
                    "cwd": report.ROOT,
                    "capture_output": True,
                    "text": True,
                    "check": False,
                    "shell": False,
                },
            )
        ]


class TestMakefileWiring:
    def test_report_target_is_phony_documented_and_read_only_dry_run(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
        assert "tech-debt-report" in phony
        assert "make tech-debt-report" in makefile

        make = shutil.which("make")
        if make is None:
            pytest.skip("make is not available")
        result = subprocess.run(
            [make, "-n", "tech-debt-report"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "scripts/ci/render_tech_debt.py" in result.stdout
