from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
CTP_ROOT = ROOT / "bt_api" / "bt_api_ctp"


def test_core_reference_rejects_the_stale_ctp_wheel() -> None:
    sdk_metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirement_text = next(
        entry
        for entry in sdk_metadata["project"]["optional-dependencies"]["core-reference"]
        if entry.lower().startswith("bt_api_ctp")
    )
    requirement = Requirement(requirement_text)

    assert requirement.specifier.contains("2.0.3") is False
    assert requirement.specifier.contains("2.0.4") is True


def test_ctp_candidate_version_matches_the_sdk_minimum() -> None:
    ctp_metadata = tomllib.loads((CTP_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_init = (CTP_ROOT / "src" / "bt_api_ctp" / "__init__.py").read_text(encoding="utf-8")
    bundle_config = tomllib.loads(
        (ROOT / "bt_api_py" / "configs" / "exchange-bundles.toml").read_text(encoding="utf-8")
    )
    ctp_bundle = next(
        venue
        for venue in bundle_config["bundles"]["core-reference"]["venues"]
        if venue["package"] == "bt_api_ctp"
    )
    candidate = Version(ctp_metadata["project"]["version"])
    required = Version(ctp_bundle["min_version"])
    core_reference = next(
        Requirement(entry)
        for entry in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["optional-dependencies"]["core-reference"]
        if entry.lower().startswith("bt_api_ctp")
    )

    assert f'__version__ = "{candidate}"' in package_init
    assert candidate >= Version("2.0.4")
    assert candidate >= required
    assert core_reference.specifier.contains(str(candidate))
