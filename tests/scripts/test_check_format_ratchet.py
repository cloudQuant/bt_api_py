"""Offline contracts for the per-submodule format debt ratchet."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.ci import check_format_ratchet as ratchet

ROOT = Path(__file__).resolve().parents[2]


class TestFormatOutput:
    def test_counts_ruff_summary_including_markdown_files(self):
        output = """unformatted: File would be reformatted
 --> bt_api/bt_api_base/README.md:115:1

33 files would be reformatted, 145 files already formatted
"""

        assert ratchet.parse_format_output(output, returncode=1) == 33

    def test_all_formatted_summary_has_zero_debt(self):
        assert ratchet.parse_format_output("1 file already formatted\n", 0) == 0

    @pytest.mark.parametrize(
        ("output", "returncode"),
        [
            ("unexpected output\n", 0),
            ("1 file would be reformatted\n", 0),
            ("1 file already formatted\n", 1),
            ("1 file already formatted\n", 2),
        ],
    )
    def test_malformed_or_inconsistent_ruff_output_fails_closed(self, output, returncode):
        with pytest.raises(ratchet.FormatScanError):
            ratchet.parse_format_output(output, returncode)


class TestComparison:
    def test_format_debt_growth_is_a_regression(self):
        result = ratchet.compare_format_counts({"bt_api_base": 34}, {"bt_api_base": 33})

        assert result.ok is False
        assert result.regressions == ["bt_api_base: 34 > baseline 33 (+1)"]

    def test_format_debt_decrease_is_an_improvement(self):
        result = ratchet.compare_format_counts({"bt_api_base": 30}, {"bt_api_base": 33})

        assert result.ok is True
        assert result.improvements == ["bt_api_base: 30 < baseline 33 (-3)"]

    def test_new_module_fails_even_when_its_count_is_zero(self):
        result = ratchet.compare_format_counts(
            {"bt_api_base": 33, "bt_api_new": 0}, {"bt_api_base": 33}
        )

        assert result.ok is False
        assert result.regressions == ["bt_api_new: unrecorded module"]

    def test_missing_recorded_module_fails(self):
        result = ratchet.compare_format_counts(
            {"bt_api_base": 33}, {"bt_api_base": 33, "bt_api_okx": 86}
        )

        assert result.ok is False
        assert result.regressions == ["bt_api_okx: missing from current scope"]


class TestScopeAndScan:
    def test_default_scope_discovers_only_bt_api_directories(self, tmp_path):
        submodules = tmp_path / "bt_api"
        (submodules / "bt_api_okx").mkdir(parents=True)
        (submodules / "bt_api_base" / "src").mkdir(parents=True)
        (submodules / "not_an_adapter").mkdir()
        (submodules / "bt_api_file").write_text("not a directory", encoding="utf-8")

        assert ratchet.default_scope(tmp_path) == ["bt_api_base", "bt_api_okx"]

    def test_each_module_uses_one_exact_format_check_command(self, monkeypatch):
        calls = []

        def fake_ruff(*args):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "1 file already formatted\n", "")

        monkeypatch.setattr(ratchet, "_ruff", fake_ruff)

        assert ratchet.scan_module("bt_api_example") == 0
        assert calls == [("format", "--check", "bt_api/bt_api_example")]

    def test_ruff_process_uses_fixed_root_and_never_a_shell(self, monkeypatch):
        calls = []
        completed = subprocess.CompletedProcess(["ruff", "--version"], 0, "ruff 0.16.2\n", "")

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return completed

        monkeypatch.setattr(ratchet.subprocess, "run", fake_run)

        assert ratchet._run_ruff(["ruff", "--version"]) is completed
        assert calls == [
            (
                ["ruff", "--version"],
                {
                    "cwd": ratchet.ROOT,
                    "capture_output": True,
                    "text": True,
                    "check": False,
                    "shell": False,
                },
            )
        ]

    def test_ruff_uses_path_fallback_only_when_module_is_missing(self, monkeypatch):
        calls = []
        fallback = subprocess.CompletedProcess(["ruff", "--version"], 0, "ruff 0.16.2\n", "")

        def fake_run(command):
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 1, "", "No module named ruff")
            return fallback

        monkeypatch.setattr(ratchet, "_run_ruff", fake_run)

        assert ratchet._ruff("--version") is fallback
        assert calls == [
            [ratchet.sys.executable, "-m", "ruff", "--version"],
            ["ruff", "--version"],
        ]


class TestBaseline:
    def test_committed_baseline_has_schema_scope_and_all_module_counts(self):
        payload = ratchet.load_baseline(ratchet.DEFAULT_BASELINE)
        expected = {
            "bt_api_base": 33,
            "bt_api_binance": 100,
            "bt_api_bitget": 10,
            "bt_api_bybit": 18,
            "bt_api_coinbase": 23,
            "bt_api_ctp": 25,
            "bt_api_dydx": 20,
            "bt_api_gateio": 25,
            "bt_api_htx": 23,
            "bt_api_hyperliquid": 23,
            "bt_api_ib_web": 40,
            "bt_api_kraken": 7,
            "bt_api_mexc": 12,
            "bt_api_mt5": 7,
            "bt_api_okx": 85,
        }

        assert payload["schema_version"] == ratchet.SCHEMA_VERSION
        assert payload["generated_at"]
        assert payload["ruff_version"] == "ruff 0.16.2"
        assert payload["scope"] == {
            "pattern": "bt_api/bt_api_*",
            "command": "ruff format --check bt_api/<module>",
        }
        assert payload["modules"] == sorted(expected)
        assert payload["format"]["by_module"] == expected
        assert payload["format"]["total"] == 451
        assert payload["format"]["total"] == sum(expected.values())
        assert payload["notes"]

    def test_snapshot_builder_requires_a_count_for_every_module(self):
        with pytest.raises(ratchet.FormatRatchetError):
            ratchet.build_snapshot(["bt_api_base"], {}, "ruff 0.16.2")


class TestUpdates:
    def test_update_refuses_to_raise_baseline_and_preserves_file(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "format-ratchet.json"
        snapshot = ratchet.build_snapshot(["bt_api_base"], {"bt_api_base": 5}, "ruff 0.16.2")
        original = json.dumps(snapshot, indent=2) + "\n"
        baseline_path.write_text(original, encoding="utf-8")
        monkeypatch.setattr(ratchet, "default_scope", lambda root=ratchet.ROOT: ["bt_api_base"])
        monkeypatch.setattr(ratchet, "scan_modules", lambda modules: {"bt_api_base": 6})
        monkeypatch.setattr(ratchet, "ruff_version", lambda: "ruff 0.16.2")

        assert ratchet.main(["--baseline", str(baseline_path), "--update"]) == 1
        assert baseline_path.read_text(encoding="utf-8") == original

    def test_force_update_explicitly_replaces_counts_and_module_scope(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "format-ratchet.json"
        snapshot = ratchet.build_snapshot(["bt_api_base"], {"bt_api_base": 5}, "ruff 0.16.2")
        baseline_path.write_text(json.dumps(snapshot), encoding="utf-8")
        monkeypatch.setattr(
            ratchet,
            "default_scope",
            lambda root=ratchet.ROOT: ["bt_api_base", "bt_api_new"],
        )
        monkeypatch.setattr(
            ratchet, "scan_modules", lambda modules: {"bt_api_base": 6, "bt_api_new": 2}
        )
        monkeypatch.setattr(ratchet, "ruff_version", lambda: "ruff 0.16.2")

        assert ratchet.main(["--baseline", str(baseline_path), "--force-update"]) == 0
        updated = ratchet.load_baseline(baseline_path)
        assert updated["modules"] == ["bt_api_base", "bt_api_new"]
        assert updated["format"]["by_module"] == {"bt_api_base": 6, "bt_api_new": 2}

    def test_print_scope_emits_paths_without_running_ruff(self, monkeypatch, capsys):
        monkeypatch.setattr(ratchet, "default_scope", lambda root=ratchet.ROOT: ["bt_api_base"])
        monkeypatch.setattr(
            ratchet, "scan_modules", lambda modules: pytest.fail("scope output must not scan")
        )

        assert ratchet.main(["--print-scope"]) == 0
        assert capsys.readouterr().out.strip() == "bt_api/bt_api_base"


class TestMakefileAndWorkflowWiring:
    def test_makefile_targets_and_dry_runs_call_the_script(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
        assert "format-ratchet" in phony
        assert "format-ratchet-update" in phony
        assert "make format-ratchet " in makefile
        assert "make format-ratchet-update " in makefile

        make = shutil.which("make")
        if make is None:
            pytest.skip("make is not available")
        for target in ("format-ratchet", "format-ratchet-update"):
            result = subprocess.run(
                [make, "-n", target], cwd=ROOT, capture_output=True, text=True, check=False
            )
            assert result.returncode == 0, result.stderr
            assert "scripts/ci/check_format_ratchet.py" in result.stdout

    def test_ci_has_separate_blocking_pinned_ratcheting_job(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^  format-ratchet:\n(?P<job>.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
            workflow,
        )
        assert match is not None, "a separate format-ratchet job is required"
        job = match.group("job")
        assert "continue-on-error:" not in job
        assert "submodules: recursive" in job
        assert "ruff==0.16.2" in job
        assert "python scripts/ci/check_format_ratchet.py" in job
        assert "check_quality_ratchet.py" not in job

    def test_quality_gate_requires_and_reports_format_ratchet(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^  quality-gate:\n(?P<job>.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
            workflow,
        )
        assert match is not None, "the quality-gate job is required"
        job = match.group("job")

        needs_match = re.search(r"(?ms)^    needs:(?P<value>.*?)(?=^    [a-zA-Z0-9_-]+:|\Z)", job)
        assert needs_match is not None, "quality-gate must declare its job dependencies"
        assert "format-ratchet" in re.findall(r"[a-zA-Z0-9_-]+", needs_match.group("value"))

        result_check = re.search(
            r'(?ms)^[ \t]*if[ \t]+\[[ \t]*"\$\{\{[ \t]*needs\.format-ratchet\.result'
            r'[ \t]*\}\}"[ \t]*!=[ \t]*"success"[ \t]*\];[ \t]*then[ \t]*\n'
            r"(?P<body>.*?)(?=^[ \t]*fi[ \t]*$)",
            job,
        )
        assert result_check is not None, (
            "quality-gate must fail when format-ratchet is not successful"
        )
        assert re.search(r"(?m)^[ \t]*exit[ \t]+1[ \t]*$", result_check.group("body"))

        summary_match = re.search(
            r"(?ms)^      - name: Generate summary\n(?P<step>.*?)(?=^      - name:|\Z)",
            job,
        )
        assert summary_match is not None, "quality-gate must generate its summary"
        summary_line = next(
            (
                line
                for line in summary_match.group("step").splitlines()
                if "Submodule format ratchet" in line
            ),
            None,
        )
        assert summary_line is not None
        assert re.search(r"\$\{\{\s*needs\.format-ratchet\.result\b", summary_line)
        assert "'Passed' || 'Failed'" in summary_line

    def test_existing_submodule_format_job_remains_report_only(self):
        workflow = (ROOT / ".github/workflows/submodule-tests.yml").read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^  submodule-format:\n(?P<job>.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
            workflow,
        )
        assert match is not None
        assert "continue-on-error: true" in match.group("job")
