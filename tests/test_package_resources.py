"""Installed-package resource contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
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


def test_core_reference_ci_supplement_uses_an_immutable_public_okx_source() -> None:
    """Keep unpublished adapters out of PyPI metadata and reproducible in CI."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    dependencies = config["project"]["optional-dependencies"]["core-reference"]
    assert not any(item.startswith("bt_api_okx") for item in dependencies)

    okx_requirement = (REPOSITORY_ROOT / "requirements-ci-core-reference.txt").read_text(
        encoding="utf-8"
    )

    assert (
        "bt_api_okx @ "
        "https://github.com/cloudQuant/bt_api_okx/archive/"
        "d407ba69f40f775f4d6aa4d07c9a96f94ddb5263.tar.gz"
    ) in okx_requirement


def test_dev_extra_declares_no_isolation_build_toolchain() -> None:
    """The full suite invokes ``python -m build --no-isolation`` directly."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    build_system_requirements = config["build-system"]["requires"]
    dev_dependencies = config["project"]["optional-dependencies"]["dev"]
    project_dependencies = config["project"]["dependencies"]

    assert {"setuptools>=64", "wheel", "cython", "numpy"}.issubset(build_system_requirements)
    assert {"build>=1.0.0", "setuptools>=83.0.0", "wheel", "cython"}.issubset(dev_dependencies)
    assert "numpy>=1.26.0" in project_dependencies


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
    tests_data = yaml.safe_load(tests_workflow)
    full_suite_steps = tests_data["jobs"]["full-suite"]["steps"]
    full_suite_install = next(
        step for step in full_suite_steps if step.get("name") == "Install package + dev deps"
    )
    assert '".[dev,security,core-reference]"' in full_suite_install["run"]
    assert "requirements-ci-core-reference.txt" in full_suite_install["run"]
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

    smoke_job = publish_data["jobs"]["smoke-install-testpypi"]
    assert smoke_job["needs"] == ["build", "publish-testpypi"]
    smoke_steps = smoke_job["steps"]
    assert smoke_steps[0]["uses"] == "actions/checkout@v6"
    assert smoke_steps[0]["with"]["ref"] == "${{ inputs.expected_sha }}"

    smoke_install = next(
        step
        for step in smoke_steps
        if step.get("name") == "Install candidate in a fresh virtualenv and smoke test"
    )
    smoke_install_run = smoke_install["run"]
    assert smoke_install["working-directory"] == "${{ runner.temp }}"
    assert "needs.build.outputs.version" in smoke_install["env"]["VERSION"]
    assert 'test -n "$VERSION"' in smoke_install_run
    assert '"bt_api_py[core-reference]==$VERSION"' in smoke_install_run
    assert '-r "$GITHUB_WORKSPACE/requirements-ci-core-reference.txt"' in smoke_install_run
    assert "--index-url https://test.pypi.org/simple/" in smoke_install_run
    assert "--extra-index-url https://pypi.org/simple/" in smoke_install_run

    doctor_contract = next(
        step
        for step in smoke_steps
        if step.get("name") == "Validate core-reference doctor contract"
    )
    doctor_contract_run = doctor_contract["run"]
    assert doctor_contract["working-directory"] == "${{ runner.temp }}"
    assert "doctor-core-reference.json" in doctor_contract_run
    assert "json.loads" in doctor_contract_run
    assert 'required = {"binance", "okx", "ctp"}' in doctor_contract_run
    for field in ("installed", "version_ok", "entry_point"):
        assert f'venue["{field}"] is True' in doctor_contract_run
    assert "importlib.import_module" in doctor_contract_run
    assert "bt_api_py.__file__" in doctor_contract_run
    assert 'os.environ["GITHUB_WORKSPACE"]' in doctor_contract_run
    assert "not package_file.is_relative_to(workspace)" in doctor_contract_run
    for module_name in ("bt_api_py", "bt_api_binance", "bt_api_okx", "bt_api_ctp"):
        assert f'"{module_name}"' in doctor_contract_run


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
