"""Tests for artifact-first isolated submodule validation."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.submodule_validation import package_names_for_profile, run_validation


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

    payload = run_validation(
        profile="core-reference",
        repository_root=tmp_path,
        artifacts_dir=artifacts,
        config_path=config,
    )

    results = {item["package"]: item for item in payload["packages"]}
    assert results["bt_api_good"]["status"] == "passed"
    assert results["bt_api_good"]["phases"]["build"]["status"] == "passed"
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
    _write_package(tmp_path, "bt_api_base", dependencies=("pytz>=2023.3",))
    _write_package(tmp_path, "bt_api_good", with_tests=True, test_import="pytz")
    config = _write_config(tmp_path)

    payload = run_validation(
        profile="core-reference",
        repository_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        config_path=config,
    )

    results = {item["package"]: item for item in payload["packages"]}
    assert results["bt_api_good"]["status"] == "passed"
