"""Tests for the reproducible repository + plugin baseline inventory.

Task 0.1 of the unified-api-zmq-gateway acceptance iteration plan requires a
read-only manifest that explains the relationship between the parent repo, its
submodules, installed plugins and every pin/checkout divergence. These tests
drive ``scripts/verify_repository_baseline.py`` and assert the manifest is
complete and never silently hides a pin mismatch.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_repository_baseline.py"
GIT = shutil.which("git") or "git"

VALID_STATUSES = {"installed", "loadable", "certified", "experimental", "retired"}


def _generate_manifest(tmp_path: Path) -> dict[str, Any]:
    out = tmp_path / "baseline.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", str(out)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"script failed: {proc.stderr}"
    return json.loads(out.read_text())


def _gitmodules_paths() -> list[str]:
    text = (REPO_ROOT / ".gitmodules").read_text()
    return [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().startswith("path = ")
    ]


def test_manifest_contains_parent_commit(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    assert manifest["schema_version"] == 1
    commit = manifest["parent"]["commit"]
    assert isinstance(commit, str)
    assert len(commit) == 40
    assert int(commit, 16) >= 0  # valid hex sha


def test_manifest_covers_every_gitmodules_path(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    expected = _gitmodules_paths()
    actual = [s["path"] for s in manifest["submodules"]]
    # .gitmodules is the single source of truth; no magic numbers.
    assert expected
    assert set(expected) == set(actual)
    assert len(actual) == len(expected)


def test_manifest_has_required_submodule_fields(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    required = {"path", "pinned_commit", "checked_out_commit", "dirty", "pin_mismatch"}
    for submodule in manifest["submodules"]:
        assert required <= set(submodule)
        for commit_field in ("pinned_commit", "checked_out_commit"):
            assert len(submodule[commit_field]) == 40


def test_pin_mismatch_is_never_silently_ignored(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    for submodule in manifest["submodules"]:
        assert submodule["pin_mismatch"] == (
            submodule["pinned_commit"] != submodule["checked_out_commit"]
        )


def test_ctp_pin_divergence_is_reported(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    ctp = next(s for s in manifest["submodules"] if s["path"] == "bt_api/bt_api_ctp")
    # Independently re-derive the gitlink and checkout to cross-check the manifest.
    pinned = subprocess.check_output(
        [GIT, "ls-tree", "HEAD", "bt_api/bt_api_ctp"],
        cwd=REPO_ROOT,
        text=True,
    ).split()[2]
    checked = subprocess.check_output(
        [GIT, "-C", "bt_api/bt_api_ctp", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    assert ctp["pinned_commit"] == pinned
    assert ctp["checked_out_commit"] == checked
    assert ctp["pin_mismatch"] == (pinned != checked)


def test_manifest_lists_plugins_with_valid_status(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    plugins = manifest["plugins"]
    assert isinstance(plugins, list)
    assert plugins, "installed plugin entry points should be non-empty"
    for plugin in plugins:
        assert plugin["name"]
        assert plugin["status"] in VALID_STATUSES
        assert plugin["installed"] is True


def test_summary_counts_are_consistent(tmp_path: Path) -> None:
    manifest = _generate_manifest(tmp_path)
    submodules = manifest["submodules"]
    summary = manifest["summary"]
    assert summary["submodule_count"] == len(submodules)
    assert summary["plugin_count"] == len(manifest["plugins"])
    assert set(summary["pin_mismatch_submodules"]) == {
        s["path"] for s in submodules if s["pin_mismatch"]
    }
    assert set(summary["dirty_submodules"]) == {s["path"] for s in submodules if s["dirty"]}
