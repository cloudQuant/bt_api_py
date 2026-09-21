"""Plugin catalog contract tests (Task 2.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bt_api_py._plugin_catalog import PluginCatalog

if TYPE_CHECKING:
    from pathlib import Path

VALID_STATUSES = {"installed", "loadable", "certified", "missing"}


def test_catalog_lists_core_reference_bundle() -> None:
    catalog = PluginCatalog()
    assert "core-reference" in catalog.list_bundles()


def test_catalog_resolves_core_reference_venues() -> None:
    catalog = PluginCatalog()
    result = catalog.resolve_bundle("core-reference")
    assert result["name"] == "core-reference"
    packages = {v["package"] for v in result["venues"]}
    assert {"bt_api_binance", "bt_api_okx", "bt_api_ctp"} == packages


def test_catalog_reports_per_venue_status() -> None:
    catalog = PluginCatalog()
    result = catalog.resolve_bundle("core-reference")
    assert result["venues"], "bundle must list venues"
    for venue in result["venues"]:
        assert venue["status"] in VALID_STATUSES
        assert venue["package"]
        assert venue["exchange"]


def test_catalog_unknown_bundle_raises() -> None:
    catalog = PluginCatalog()
    with pytest.raises(ValueError):
        catalog.resolve_bundle("nonexistent")


def _write_bundle_config(tmp_path: Path, *, certification: str = "certified") -> PluginCatalog:
    bundles_path = tmp_path / "bundles.toml"
    bundles_path.write_text(
        f"""
[bundles.test]
description = "test bundle"

[[bundles.test.venues]]
package = "example-package"
plugin = "example-plugin"
exchange = "Example"
min_version = "2.0.0"
certification = "{certification}"
""",
        encoding="utf-8",
    )
    return PluginCatalog(bundles_path)


@pytest.mark.parametrize(
    ("version", "entry_point", "expected_status"),
    [
        ("1.0.0", True, "loadable"),
        ("2.0.0", False, "installed"),
    ],
)
def test_card_does_not_override_version_or_entry_point_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    entry_point: bool,
    expected_status: str,
) -> None:
    from bt_api_py import _plugin_catalog

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "example-plugin.md").write_text("evidence", encoding="utf-8")
    monkeypatch.setattr(_plugin_catalog, "CERTIFIED_CARDS_DIR", cards_dir)
    monkeypatch.setattr(
        PluginCatalog,
        "_installed_version",
        staticmethod(lambda package: (True, version)),
    )
    monkeypatch.setattr(
        PluginCatalog,
        "_has_entry_point",
        staticmethod(lambda plugin: entry_point),
    )

    venue = _write_bundle_config(tmp_path).resolve_bundle("test")["venues"][0]

    assert venue["status"] == expected_status


def test_all_certification_requirements_resolve_as_certified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_api_py import _plugin_catalog

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "example-plugin.md").write_text("evidence", encoding="utf-8")
    monkeypatch.setattr(_plugin_catalog, "CERTIFIED_CARDS_DIR", cards_dir)
    monkeypatch.setattr(
        PluginCatalog,
        "_installed_version",
        staticmethod(lambda package: (True, "2.0.0")),
    )
    monkeypatch.setattr(
        PluginCatalog,
        "_has_entry_point",
        staticmethod(lambda plugin: True),
    )

    venue = _write_bundle_config(tmp_path).resolve_bundle("test")["venues"][0]

    assert venue["certification"] == "certified"
    assert venue["installed"] is True
    assert venue["version_ok"] is True
    assert venue["entry_point"] is True
    assert venue["status"] == "certified"


def test_experimental_config_cannot_resolve_as_certified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_api_py import _plugin_catalog

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "example-plugin.md").write_text("evidence", encoding="utf-8")
    monkeypatch.setattr(_plugin_catalog, "CERTIFIED_CARDS_DIR", cards_dir)
    monkeypatch.setattr(
        PluginCatalog,
        "_installed_version",
        staticmethod(lambda package: (True, "2.0.0")),
    )
    monkeypatch.setattr(
        PluginCatalog,
        "_has_entry_point",
        staticmethod(lambda plugin: True),
    )

    venue = _write_bundle_config(tmp_path, certification="experimental").resolve_bundle("test")[
        "venues"
    ][0]

    assert venue["status"] == "loadable"


def test_missing_evidence_card_keeps_valid_plugin_loadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_api_py import _plugin_catalog

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    monkeypatch.setattr(_plugin_catalog, "CERTIFIED_CARDS_DIR", cards_dir)
    monkeypatch.setattr(
        PluginCatalog,
        "_installed_version",
        staticmethod(lambda package: (True, "2.0.0")),
    )
    monkeypatch.setattr(
        PluginCatalog,
        "_has_entry_point",
        staticmethod(lambda plugin: True),
    )

    venue = _write_bundle_config(tmp_path).resolve_bundle("test")["venues"][0]

    assert venue["status"] == "loadable"


def test_uninstalled_plugin_with_evidence_card_remains_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_api_py import _plugin_catalog

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "example-plugin.md").write_text("evidence", encoding="utf-8")
    monkeypatch.setattr(_plugin_catalog, "CERTIFIED_CARDS_DIR", cards_dir)
    monkeypatch.setattr(
        PluginCatalog,
        "_installed_version",
        staticmethod(lambda package: (False, None)),
    )
    monkeypatch.setattr(
        PluginCatalog,
        "_has_entry_point",
        staticmethod(lambda plugin: True),
    )

    venue = _write_bundle_config(tmp_path).resolve_bundle("test")["venues"][0]

    assert venue["status"] == "missing"


def test_evidence_card_directory_is_not_accepted_as_a_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bt_api_py import _plugin_catalog

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    (cards_dir / "example-plugin.md").mkdir()
    monkeypatch.setattr(_plugin_catalog, "CERTIFIED_CARDS_DIR", cards_dir)
    monkeypatch.setattr(
        PluginCatalog,
        "_installed_version",
        staticmethod(lambda package: (True, "2.0.0")),
    )
    monkeypatch.setattr(
        PluginCatalog,
        "_has_entry_point",
        staticmethod(lambda plugin: True),
    )

    venue = _write_bundle_config(tmp_path).resolve_bundle("test")["venues"][0]

    assert venue["status"] == "loadable"
