"""Installed-package resource contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from importlib.resources import files
from pathlib import Path

import yaml

from bt_api_py._plugin_catalog import PluginCatalog

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WHEEL_CONTRACT_SCRIPT = REPOSITORY_ROOT / "scripts" / "ci" / "verify_wheel_contract.py"


def _build_subprocess_env() -> dict[str, str]:
    """Keep isolated build probes out of a parent pytest-cov session."""

    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("COV_CORE_") or key in {"COVERAGE_FILE", "COVERAGE_PROCESS_START"}:
            env.pop(key, None)
    return env


def test_default_bundle_catalog_uses_packaged_resource() -> None:
    resource = files("bt_api_py.configs").joinpath("exchange-bundles.toml")

    assert resource.is_file()
    assert "core-reference" in PluginCatalog().list_bundles()


def test_source_root_does_not_define_a_second_bundle_catalog() -> None:
    repository_root = Path(__file__).resolve().parents[1]

    assert not (repository_root / "configs" / "exchange-bundles.toml").exists()


def test_wheel_contract_checker_runs_doctor_from_an_installed_wheel(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist_dir)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        env=_build_subprocess_env(),
        text=True,
    )
    assert build.returncode == 0, build.stderr

    receipt_path = tmp_path / "wheel-receipt.json"
    verify = subprocess.run(
        [
            sys.executable,
            str(WHEEL_CONTRACT_SCRIPT),
            "--dist-dir",
            str(dist_dir),
            "--receipt",
            str(receipt_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        env=_build_subprocess_env(),
        text=True,
    )

    assert verify.returncode == 0, verify.stderr
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["result"] == "passed"
    assert (
        receipt["head_sha"]
        == subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - Git is required by the source worktree test.
            cwd=REPOSITORY_ROOT,
            text=True,
        ).strip()
    )
    assert receipt["generated_at"].endswith("Z")
    assert receipt["resource_sha256"]["source"] == receipt["resource_sha256"]["wheel"]
    assert receipt["resource_sha256"]["source"] == receipt["resource_sha256"]["sdist"]
    assert receipt["doctor"]["exit_code"] == 0
    assert receipt["doctor"]["payload"]["name"] == "core-reference"
    assert "site-packages/bt_api_py" in receipt["package_file"].replace("\\", "/")


def test_ci_workflows_enforce_the_installed_wheel_contract() -> None:
    tests_workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )
    publish_workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts/ci/verify_wheel_contract.py" in tests_workflow
    assert "bt_api_py.doctor --bundle core-reference --format json" in publish_workflow

    publish_data = yaml.safe_load(publish_workflow)
    publish_steps = publish_data["jobs"]["build"]["steps"]
    wheel_contract_step = next(
        step
        for step in publish_steps
        if step.get("name") == "Verify installed wheel resource contract"
    )
    wheel_contract_run = wheel_contract_step["run"]

    assert "python scripts/ci/verify_wheel_contract.py" in wheel_contract_run
    assert "--dist-dir dist" in wheel_contract_run
    assert "--receipt dist-meta/wheel-contract-receipt.json" in wheel_contract_run
    assert "+" not in wheel_contract_run


def test_built_wheel_contains_catalog_but_not_bytecode(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(dist_dir)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        env=_build_subprocess_env(),
        text=True,
    )
    assert build.returncode == 0, build.stderr

    wheel = next(dist_dir.glob("bt_api_py-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()

    assert "bt_api_py/configs/exchange-bundles.toml" in members
    assert not any("__pycache__" in member or member.endswith(".pyc") for member in members)
