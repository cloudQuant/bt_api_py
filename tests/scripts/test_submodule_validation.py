"""Tests for artifact-first isolated submodule validation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from scripts.ci import submodule_validation
from scripts.ci.offline_pip import WheelhousePathError, pip_source_environment
from scripts.ci.submodule_validation import (
    PhaseResult,
    _artifact_subprocess_env,
    package_names_for_profile,
    run_validation,
    validate_package,
)
from tests.offline_wheelhouse import build_validator_wheelhouse


def test_artifact_subprocess_env_excludes_parent_coverage_hooks(monkeypatch) -> None:
    monkeypatch.setenv("COV_CORE_SOURCE", "bt_api_py")
    monkeypatch.setenv("COVERAGE_FILE", "/tmp/parent-coverage")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "/tmp/coveragerc")

    environment = _artifact_subprocess_env(
        {"COV_CORE_CONFIG": "/tmp/child-coveragerc", "PYTHONPATH": "/tmp/package"}
    )

    assert "COV_CORE_SOURCE" not in environment
    assert "COV_CORE_CONFIG" not in environment
    assert "COVERAGE_FILE" not in environment
    assert "COVERAGE_PROCESS_START" not in environment
    assert environment["PYTHONPATH"] == "/tmp/package"


def test_artifact_subprocess_env_does_not_inherit_parent_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/tmp/parent-package")

    environment = _artifact_subprocess_env()

    assert "PYTHONPATH" not in environment


def test_artifact_subprocess_env_uses_only_the_explicit_wheelhouse(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIP_INDEX_URL", "https://invalid.example/simple")
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://invalid.example/extra")
    monkeypatch.setenv("PIP_FIND_LINKS", "https://invalid.example/wheels")
    monkeypatch.setenv("PIP_TRUSTED_HOST", "invalid.example")
    monkeypatch.setenv("PIP_NO_CACHE_DIR", "0")

    environment = _artifact_subprocess_env(wheelhouse=tmp_path)

    assert environment["PIP_NO_INDEX"] == "1"
    assert environment["PIP_FIND_LINKS"] == str(tmp_path.resolve())
    assert environment["PIP_NO_CACHE_DIR"] == "1"
    assert environment["PIP_CONFIG_FILE"] == os.devnull
    assert not {"PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST"} & set(environment)
    assert {key for key in environment if key.startswith("PIP_")} == {
        "PIP_CONFIG_FILE",
        "PIP_NO_INDEX",
        "PIP_FIND_LINKS",
        "PIP_NO_CACHE_DIR",
    }


def test_pip_source_environment_copies_a_read_only_mapping(tmp_path: Path) -> None:
    source = MappingProxyType(
        {
            "PATH": "/usr/bin",
            "PIP_INDEX_URL": "https://invalid.example/simple",
            "PIP_FIND_LINKS": "https://invalid.example/wheels",
        }
    )
    original = dict(source)

    environment = pip_source_environment(source, tmp_path)

    assert dict(source) == original
    assert environment is not source
    assert environment["PATH"] == "/usr/bin"
    assert environment["PIP_FIND_LINKS"] == str(tmp_path)
    assert {key for key in environment if key.startswith("PIP_")} == {
        "PIP_CONFIG_FILE",
        "PIP_NO_INDEX",
        "PIP_FIND_LINKS",
        "PIP_NO_CACHE_DIR",
    }


def test_pip_source_environment_rejects_non_mapping_input() -> None:
    error = pytest.raises(TypeError, pip_source_environment, [], None)

    assert str(error.value) == "environment must be a mapping"


@pytest.mark.parametrize("wheelhouse_kind", ("relative-string", "missing-path"))
def test_run_validation_rejects_invalid_wheelhouse_before_creating_artifacts(
    tmp_path: Path, wheelhouse_kind: str
) -> None:
    artifacts = tmp_path / "artifacts"
    wheelhouse: Path | str
    if wheelhouse_kind == "relative-string":
        wheelhouse = "relative-wheelhouse"
    else:
        wheelhouse = tmp_path / "missing-wheelhouse"

    with pytest.raises(WheelhousePathError):
        run_validation(
            profile="core-reference",
            repository_root=tmp_path,
            artifacts_dir=artifacts,
            config_path=tmp_path / "missing-config.toml",
            wheelhouse=wheelhouse,
        )
    assert not artifacts.exists()


def test_submodule_validation_cli_loads_from_an_unrelated_working_directory(
    tmp_path: Path,
) -> None:
    script = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "scripts", "ci", "submodule_validation.py"
        )
    )
    result = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--wheelhouse" in result.stdout


def _write_package(
    root: Path,
    name: str,
    *,
    importable: bool = True,
    with_tests: bool = False,
    dependencies: tuple[str, ...] = (),
    test_import: str | None = None,
) -> None:
    package_dir = root / "bt_api" / name
    package_dir.mkdir(parents=True)
    dependency_block = ""
    if dependencies:
        dependency_block = (
            "dependencies = [\n"
            + "".join(f'    "{dependency}",\n' for dependency in dependencies)
            + "]\n"
        )
    (package_dir / "pyproject.toml").write_text(
        f"""[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = \"{name}\"
version = \"0.0.1\"
{dependency_block}""",
        encoding="utf-8",
    )
    if importable:
        module = package_dir / name
        module.mkdir()
        (module / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    if with_tests:
        tests_dir = package_dir / "tests"
        tests_dir.mkdir()
        imported_module = f"import {test_import}\n" if test_import else ""
        assertion = (
            f"    assert {test_import}\n" if test_import else "    assert pytest.__version__\n"
        )
        (tests_dir / "test_environment.py").write_text(
            "import pytest\n"
            + imported_module
            + "\n\ndef test_runtime_dependencies_are_available() -> None:\n"
            + assertion,
            encoding="utf-8",
        )


def _write_config(root: Path) -> Path:
    config = root / "configs" / "submodule-validation.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """[profiles.core-reference]
bundle = "core-reference"
include_base = true
""",
        encoding="utf-8",
    )
    catalog = root / "bt_api_py" / "configs" / "exchange-bundles.toml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        """[bundles.core-reference]
[[bundles.core-reference.venues]]
package = "bt_api_good"
[[bundles.core-reference.venues]]
package = "bt_api_missing"
""",
        encoding="utf-8",
    )
    return config


def test_profile_resolution_uses_the_bundle_catalog_not_a_fixed_plugin_count(
    tmp_path: Path,
) -> None:
    config = {"profiles": {"core-reference": {"bundle": "core-reference", "include_base": True}}}
    catalog = {
        "bundles": {
            "core-reference": {"venues": [{"package": "bt_api_good"}, {"package": "bt_api_good"}]}
        }
    }

    assert package_names_for_profile("core-reference", config, catalog, tmp_path) == [
        "bt_api_base",
        "bt_api_good",
    ]


def test_validation_emits_json_junit_and_per_phase_logs_for_unavailable_package(
    tmp_path: Path,
) -> None:
    wheelhouse = build_validator_wheelhouse(tmp_path / "wheelhouse")
    _write_package(tmp_path, "bt_api_base")
    _write_package(tmp_path, "bt_api_good", with_tests=True)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\naddopts = '--strict-markers'\n",
        encoding="utf-8",
    )
    (tmp_path / "conftest.py").write_text(
        "raise RuntimeError('parent conftest leaked into isolated package validation')\n",
        encoding="utf-8",
    )
    network_tests = tmp_path / "bt_api" / "bt_api_good" / "tests" / "network"
    network_tests.mkdir()
    (network_tests / "test_live_service.py").write_text(
        "raise RuntimeError('network tests must not run in an offline validation profile')\n",
        encoding="utf-8",
    )
    (tmp_path / "bt_api" / "bt_api_good" / "tests" / "test_socket_isolation.py").write_text(
        "import socket\n\n"
        "import pytest\n"
        "from pytest_socket import SocketBlockedError\n\n\n"
        "def test_socket_access_is_disabled() -> None:\n"
        "    with pytest.raises(SocketBlockedError):\n"
        "        socket.socket()\n",
        encoding="utf-8",
    )
    config = _write_config(tmp_path)
    artifacts = tmp_path / "artifacts"
    stale_base_dir = artifacts / "base-dist"
    stale_base_dir.mkdir(parents=True)
    stale_base_wheel = stale_base_dir / "bt_api_base-999.0.0-py3-none-any.whl"
    stale_base_wheel.write_bytes(b"stale base wheel")
    stale_plugin_dir = artifacts / "plugin-dist" / "bt_api_good"
    stale_plugin_dir.mkdir(parents=True)
    stale_plugin_wheel = stale_plugin_dir / "bt_api_good-999.0.0-py3-none-any.whl"
    stale_plugin_wheel.write_bytes(b"stale plugin wheel")
    legacy_venv_dir = artifacts / "venvs" / "bt_api_good"
    legacy_venv_dir.mkdir(parents=True)
    legacy_venv_marker = legacy_venv_dir / "preserve.txt"
    legacy_venv_marker.write_text("legacy venv artifact\n", encoding="utf-8")
    legacy_venv_entries = set(legacy_venv_dir.rglob("*"))

    payload = run_validation(
        profile="core-reference",
        repository_root=tmp_path,
        artifacts_dir=artifacts,
        config_path=config,
        wheelhouse=wheelhouse,
    )

    results = {item["package"]: item for item in payload["packages"]}
    assert results["bt_api_good"]["status"] == "passed"
    assert results["bt_api_good"]["phases"]["build"]["status"] == "passed"
    assert results["bt_api_good"]["phases"]["dependency_check"]["status"] == "passed"
    base_wheel = Path(results["bt_api_good"]["environment"]["base_wheel"])
    plugin_wheel = Path(results["bt_api_good"]["environment"]["plugin_wheel"])
    isolated_python = Path(results["bt_api_good"]["environment"]["isolated_python"])
    venv_run_dir = isolated_python.parent.parent
    assert base_wheel.name.startswith("bt_api_base-0.0.1-")
    assert plugin_wheel.name.startswith("bt_api_good-0.0.1-")
    assert base_wheel.is_relative_to(artifacts / "wheel-builds")
    assert plugin_wheel.is_relative_to(artifacts / "wheel-builds")
    assert venv_run_dir.name.startswith("run-")
    assert venv_run_dir.parent == artifacts / "venv-runs" / "bt_api_good"
    assert set(legacy_venv_dir.rglob("*")) == legacy_venv_entries
    assert legacy_venv_marker.read_text(encoding="utf-8") == "legacy venv artifact\n"
    assert stale_base_wheel.is_file()
    assert stale_plugin_wheel.is_file()
    assert results["bt_api_missing"]["status"] == "unavailable"
    resolve = results["bt_api_missing"]["phases"]["resolve"]
    assert (artifacts / resolve["stderr_path"]).is_file()
    assert (artifacts / "submodule-validation.json").is_file()
    assert (artifacts / "submodule-validation.junit.xml").is_file()
    assert (artifacts / "submodule-validation.md").is_file()
    assert (
        json.loads((artifacts / "submodule-validation.json").read_text())["profile"]
        == "core-reference"
    )


def test_validation_installs_declared_base_wheel_dependencies(tmp_path: Path) -> None:
    wheelhouse = build_validator_wheelhouse(tmp_path / "wheelhouse")
    _write_package(tmp_path, "bt_api_base", dependencies=("pytz>=2023.3",))
    _write_package(tmp_path, "bt_api_good", with_tests=True, test_import="pytz")
    config = _write_config(tmp_path)

    payload = run_validation(
        profile="core-reference",
        repository_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        config_path=config,
        wheelhouse=wheelhouse,
    )

    results = {item["package"]: item for item in payload["packages"]}
    assert results["bt_api_good"]["status"] == "passed"


def test_dependency_check_failure_stops_before_import_and_records_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    _write_package(tmp_path, "bt_api_good")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    base_wheel = tmp_path / "bt_api_base-0.15.4-py3-none-any.whl"
    base_wheel.touch()
    phases: list[str] = []
    commands: dict[str, list[str]] = {}
    real_run_phase = submodule_validation.run_phase

    def fake_run_phase(**kwargs) -> PhaseResult:
        phase = kwargs["phase"]
        phases.append(phase)
        commands[phase] = kwargs["command"]
        if phase == "build":
            wheel_dir = Path(kwargs["command"][kwargs["command"].index("--wheel-dir") + 1])
            wheel_dir.mkdir(parents=True, exist_ok=True)
            (wheel_dir / "bt_api_good-0.0.1-py3-none-any.whl").touch()
        if phase == "dependency_check":
            return real_run_phase(**kwargs)
        return PhaseResult(
            status="passed",
            exit_code=0,
            duration_seconds=0.0,
            stdout_path="",
            stderr_path="",
        )

    def failed_pip_check(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=1, stdout="", stderr="inconsistent installed dependencies"
        )

    monkeypatch.setattr(submodule_validation, "run_phase", fake_run_phase)
    monkeypatch.setattr(submodule_validation.subprocess, "run", failed_pip_check)

    result = validate_package(
        package="bt_api_good",
        profile="test",
        repository_root=tmp_path,
        artifacts_dir=artifacts,
        python=sys.executable,
        base_wheel=base_wheel,
    )

    dependency_check = result.phases["dependency_check"]
    assert result.status == "failed"
    assert result.classification == "dependency_check"
    assert dependency_check.status == "failed"
    assert dependency_check.exit_code == 1
    assert (artifacts / dependency_check.stderr_path).read_text() == (
        "inconsistent installed dependencies"
    )
    assert commands["dependency_check"][-3:] == ["-m", "pip", "check"]
    assert phases == ["venv", "base_install", "build", "install", "dependency_check"]
    assert "import" not in result.phases
