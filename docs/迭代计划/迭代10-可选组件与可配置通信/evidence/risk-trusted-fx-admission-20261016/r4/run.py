from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
ARTIFACT = ROOT / ".codex-tmp" / "iteration10-risk-fx-admission-20261016-r4"
BASE = ROOT.parent / "bt_api_base"
SOURCE_PATHS = (
    "src/bt_api_risk/__init__.py",
    "src/bt_api_risk/allocation_sizing.py",
    "src/bt_api_risk/core/__init__.py",
    "src/bt_api_risk/core/admission.py",
    "src/bt_api_risk/core/instrument.py",
    "tests/test_instrument_fx_admission.py",
)
NODES = (
    "tests/test_instrument_fx_admission.py::test_legacy_same_currency_metadata_and_admission_fingerprint_are_unchanged",
)


def pin(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest().upper()}


def git_output(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"git {args!r} failed with exit {completed.returncode}")
    return completed.stdout.decode("utf-8", errors="strict")


def git(cwd: Path, *args: str) -> str:
    return git_output(cwd, *args).strip()


def status(cwd: Path) -> list[str]:
    output = git_output(cwd, "status", "--short")
    return output.splitlines() if output else []


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def output_pins() -> list[dict[str, Any]]:
    paths = (
        ARTIFACT / "pytest.ini",
        ARTIFACT / "run.py",
        ARTIFACT / "run-pre.json",
        ARTIFACT / "stdout.txt",
        ARTIFACT / "stderr.txt",
        ARTIFACT / "junit.xml",
        ARTIFACT / "exit-code.txt",
    )
    return [pin(path) for path in paths if path.exists()]


def main() -> int:
    package_head = git(ROOT, "rev-parse", "HEAD")
    base_head = git(BASE, "rev-parse", "HEAD")
    source_before = [pin(ROOT / path) for path in SOURCE_PATHS]
    env = os.environ.copy()
    env.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "",
            "PYTEST_PLUGINS": "",
            "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(BASE / "src"))),
        }
    )
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "--noconftest",
        "-p",
        "no:cacheprovider",
        "-c",
        str(ARTIFACT / "pytest.ini"),
        f"--junitxml={ARTIFACT / 'junit.xml'}",
        *NODES,
    ]
    pre = {
        "evidence_level": "PACKAGE_FOCUSED_UNIT_QA_ONLY",
        "package_repo": str(ROOT),
        "package_head": package_head,
        "package_branch": git(ROOT, "branch", "--show-current"),
        "base_repo": str(BASE),
        "base_head": base_head,
        "base_status": status(BASE),
        "cwd": str(ROOT),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "runner_argv": [sys.executable, *sys.argv],
        "argv": argv,
        "environment": {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"],
            "PYTEST_ADDOPTS": env["PYTEST_ADDOPTS"],
            "PYTEST_PLUGINS": env["PYTEST_PLUGINS"],
            "PYTHONPATH": env["PYTHONPATH"],
            "PATH_sha256": hashlib.sha256(env.get("PATH", "").encode()).hexdigest().upper(),
            "TEMP": env.get("TEMP"),
            "TMP": env.get("TMP"),
            "SystemRoot": env.get("SystemRoot"),
            "inherited_environment_sha256": hashlib.sha256(
                json.dumps(sorted(env.items()), separators=(",", ":"), ensure_ascii=True).encode()
            ).hexdigest().upper(),
        },
        "selected_nodes": list(NODES),
        "source_pins": source_before,
        "git_status": status(ROOT),
        "runner_pin": pin(ARTIFACT / "run.py"),
        "pytest_config_pin": pin(ARTIFACT / "pytest.ini"),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(ARTIFACT / "run-pre.json", pre)
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    (ARTIFACT / "stdout.txt").write_bytes(completed.stdout)
    (ARTIFACT / "stderr.txt").write_bytes(completed.stderr)
    (ARTIFACT / "exit-code.txt").write_text(f"{completed.returncode}\n", encoding="ascii")
    source_after = [pin(ROOT / path) for path in SOURCE_PATHS]
    post = {
        "evidence_level": "PACKAGE_FOCUSED_UNIT_QA_ONLY",
        "package_repo": str(ROOT),
        "package_head": git(ROOT, "rev-parse", "HEAD"),
        "base_repo": str(BASE),
        "base_head": git(BASE, "rev-parse", "HEAD"),
        "source_pins": source_after,
        "source_pins_unchanged": source_before == source_after,
        "git_status": status(ROOT),
        "base_status": status(BASE),
        "pytest_exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest().upper(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest().upper(),
        "artifact_pins": output_pins(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(ARTIFACT / "run-post.json", post)
    print(json.dumps({"exit_code": completed.returncode, "source_pins_unchanged": post["source_pins_unchanged"], "artifact": str(ARTIFACT)}, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

