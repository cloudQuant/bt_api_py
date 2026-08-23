"""Tests for scripts/ci/verify_github_governance.py (plan M3 step 5).

The verifier compares a sanitized GitHub Rulesets API summary against the
in-repo manifests under .github/governance/rulesets/. Drift must exit non-zero
and name every violated expectation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "verify_github_governance.py"
MANIFEST_DIR = REPO_ROOT / ".github" / "governance" / "rulesets"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "governance"


def run_verifier(actual: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--actual",
            str(actual),
            "--manifest-dir",
            str(MANIFEST_DIR),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_fixture_has_no_drift() -> None:
    result = run_verifier(FIXTURES / "rulesets-valid.json")
    assert result.returncode == 0, f"unexpected drift:\n{result.stdout}\n{result.stderr}"
    assert "DRIFT" not in result.stdout


def test_missing_required_check_is_reported() -> None:
    result = run_verifier(FIXTURES / "rulesets-drifted.json")
    assert result.returncode == 1
    assert "required_status_checks" in result.stdout
    assert "PR Governance / Summary" in result.stdout


def test_wrong_approval_count_is_reported() -> None:
    result = run_verifier(FIXTURES / "rulesets-drifted.json")
    assert result.returncode == 1
    assert "approvals" in result.stdout


def test_unblocked_force_push_is_reported() -> None:
    result = run_verifier(FIXTURES / "rulesets-drifted.json")
    assert result.returncode == 1
    assert "non_fast_forward" in result.stdout


def test_codeowners_errors_are_reported() -> None:
    result = run_verifier(FIXTURES / "rulesets-drifted.json")
    assert result.returncode == 1
    assert "CODEOWNERS" in result.stdout


def test_manifests_are_self_consistent_json() -> None:
    for manifest in sorted(MANIFEST_DIR.glob("*.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert "target" in data, manifest.name
        assert data["enforcement"] in {"active", "disabled"}, manifest.name


def test_pending_gates_must_be_disabled() -> None:
    for manifest in sorted(MANIFEST_DIR.glob("*.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if "pending_decision_gate" in data:
            assert data["enforcement"] == "disabled", (
                f"{manifest.name}: ruleset blocked on {data['pending_decision_gate']} "
                "must not be declared active"
            )
