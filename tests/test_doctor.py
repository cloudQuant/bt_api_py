"""Doctor command tests (Task 2.1)."""

from __future__ import annotations

import json
import subprocess
import sys

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _run_doctor(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bt_api_py.doctor", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_doctor_json_output_contains_venues() -> None:
    proc = _run_doctor("--bundle", "core-reference", "--format", "json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["name"] == "core-reference"
    assert payload["venues"], "doctor JSON must list venues"
    for venue in payload["venues"]:
        assert venue["status"] in {"installed", "loadable", "certified", "missing"}


def test_doctor_text_output_is_human_readable() -> None:
    proc = _run_doctor("--bundle", "core-reference")
    assert proc.returncode == 0, proc.stderr
    assert "core-reference" in proc.stdout


def test_doctor_unknown_bundle_fails() -> None:
    proc = _run_doctor("--bundle", "nonexistent", "--format", "json")
    assert proc.returncode == 2
