"""Plugin catalog contract tests (Task 2.1)."""

from __future__ import annotations

import pytest

from bt_api_py._plugin_catalog import PluginCatalog

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
