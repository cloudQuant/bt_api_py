"""Tests for scripts/ci/validate_pr_governance.py (plan M4 step 1).

Fixture-first: ordinary dev PRs pass; normal PRs targeting master fail;
master hotfixes without risk:r3 / release:hotfix evidence fail; submodule
changes without old/new SHA evidence fail. Report-only mode never exits
non-zero but must surface every violation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "validate_pr_governance.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "governance"


def run_validator(context: dict, *, strict: bool) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--context",
        "-",
    ]
    if strict:
        args.append("--strict")
    result = subprocess.run(
        args,
        input=json.dumps(context),
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_dev_r1_fixture_passes_strict() -> None:
    result = run_validator(load_fixture("pr-dev-r1.json"), strict=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_master_hotfix_fixture_passes_strict() -> None:
    result = run_validator(load_fixture("pr-master-hotfix.json"), strict=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_submodule_bump_fixture_passes_strict() -> None:
    result = run_validator(load_fixture("pr-submodule-bump.json"), strict=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_normal_pr_targeting_master_fails_strict() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["target_branch"] = "master"
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "release:hotfix" in result.stdout


def test_master_hotfix_without_labels_fails_strict() -> None:
    context = load_fixture("pr-master-hotfix.json")
    context["labels"] = ["risk:r3"]
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "release:hotfix" in result.stdout


def test_master_hotfix_without_repro_evidence_fails_strict() -> None:
    context = load_fixture("pr-master-hotfix.json")
    context["body"] = "fix typo in order router"
    result = run_validator(context, strict=True)
    assert result.returncode == 1


def test_submodule_bump_without_sha_evidence_fails_strict() -> None:
    context = load_fixture("pr-submodule-bump.json")
    context["old_sha"] = None
    context["new_sha"] = None
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "SHA" in result.stdout


def test_missing_risk_label_fails_strict() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["labels"] = ["target:dev"]
    result = run_validator(context, strict=True)
    assert result.returncode == 1
    assert "risk:" in result.stdout


def test_report_only_mode_never_blocks_but_warns() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["target_branch"] = "master"
    result = run_validator(context, strict=False)
    assert result.returncode == 0
    assert "WARN" in result.stdout


def test_unknown_target_branch_fails_strict() -> None:
    context = load_fixture("pr-dev-r1.json")
    context["target_branch"] = "feature/rogue"
    result = run_validator(context, strict=True)
    assert result.returncode == 1
