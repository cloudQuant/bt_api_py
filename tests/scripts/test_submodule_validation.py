"""Tests for artifact-first isolated submodule validation."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.submodule_validation import package_names_for_profile, run_validation


def _write_package(root: Path, name: str, *, importable: bool = True) -> None:
    package_dir = root / "bt_api" / name
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text(
        f"""[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = \"{name}\"
version = \"0.0.1\"
""",
        encoding="utf-8",
    )
    if importable:
        module = package_dir / name
        module.mkdir()
        (module / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")


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
    _write_package(tmp_path, "bt_api_good")
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
