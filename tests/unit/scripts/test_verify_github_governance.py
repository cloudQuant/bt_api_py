"""Tests for scripts/ci/verify_github_governance.py (plan v2 M3 step 5).

The verifier compares a sanitized GitHub Rulesets API summary against the
in-repo manifests under .github/governance/rulesets/. Drift must exit non-zero
and name every violated expectation.

Two phases are covered:

* Observation period (plan §4.2.4): every shipped manifest must be disabled;
  a remotely Active ruleset without M6 evidence is drift.
* Post-M6 phase: once admins flip the manifests together with the remote
  state, policy parameters (approvals, required checks, force-push block)
  become verifiable. Tests simulate that phase on a temporary copy of the
  manifest directory so the shipped repo state stays plan-compliant.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "verify_github_governance.py"
MANIFEST_DIR = REPO_ROOT / ".github" / "governance" / "rulesets"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "governance"


def run_verifier(
    actual: Path, manifest_dir: Path = MANIFEST_DIR
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--actual",
            str(actual),
            "--manifest-dir",
            str(manifest_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


# --- Observation period: shipped manifests must stay disabled -------------


def test_valid_fixture_has_no_drift() -> None:
    """Baseline B2 reality (no remote rulesets yet) matches all-manifests-disabled."""
    result = run_verifier(FIXTURES / "rulesets-valid.json")
    assert result.returncode == 0, f"unexpected drift:\n{result.stdout}\n{result.stderr}"
    assert "DRIFT" not in result.stdout


def test_manifests_are_self_consistent_json() -> None:
    for manifest in sorted(MANIFEST_DIR.glob("*.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert "target" in data, manifest.name
        assert data["enforcement"] in {"active", "disabled"}, manifest.name


def test_blocked_manifests_must_be_disabled() -> None:
    """Any manifest gated on a decision or missing evidence may not be active."""
    for manifest in sorted(MANIFEST_DIR.glob("*.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        gated = "pending_decision_gate" in data or "activation_requires" in data
        if gated:
            assert data["enforcement"] == "disabled", (
                f"{manifest.name}: gated ruleset must stay disabled until its "
                "activation evidence lands in docs/governance/evidence/"
            )


def test_premature_activation_is_reported() -> None:
    """Active remote rulesets before M6 evidence are drift naming the gate."""
    result = run_verifier(FIXTURES / "rulesets-drifted.json")
    assert result.returncode == 1
    assert "active but manifest requires disabled" in result.stdout
    assert "dev.json" in result.stdout
    assert "code-optimization.json" in result.stdout


def test_codeowners_errors_are_reported() -> None:
    result = run_verifier(FIXTURES / "rulesets-drifted.json")
    assert result.returncode == 1
    assert "CODEOWNERS" in result.stdout


# --- Post-M6 phase: policy drift once manifests flip to active ------------


@pytest.fixture()
def activated_dev_manifest_dir(tmp_path: Path) -> Path:
    """Copy the real manifests and activate only dev.json, as an admin would
    after M6 drill evidence exists."""
    target = tmp_path / "manifests-active"
    shutil.copytree(MANIFEST_DIR, target)
    dev_manifest = target / "dev.json"
    data = json.loads(dev_manifest.read_text(encoding="utf-8"))
    data["enforcement"] = "active"
    data.pop("activation_requires", None)
    dev_manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def test_missing_required_check_is_reported(activated_dev_manifest_dir: Path) -> None:
    result = run_verifier(FIXTURES / "rulesets-policy-drifted.json", activated_dev_manifest_dir)
    assert result.returncode == 1
    assert "required_status_checks" in result.stdout
    assert "PR Governance / Summary" in result.stdout


def test_wrong_approval_count_is_reported(activated_dev_manifest_dir: Path) -> None:
    result = run_verifier(FIXTURES / "rulesets-policy-drifted.json", activated_dev_manifest_dir)
    assert result.returncode == 1
    assert "approvals" in result.stdout


def test_unblocked_force_push_is_reported(activated_dev_manifest_dir: Path) -> None:
    result = run_verifier(FIXTURES / "rulesets-policy-drifted.json", activated_dev_manifest_dir)
    assert result.returncode == 1
    assert "non_fast_forward" in result.stdout
