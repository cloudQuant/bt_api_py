"""Exchange bundle catalog (Task 2.1).

Reads ``configs/exchange-bundles.toml`` and reports per-venue install, entry
point and certification status from installed distributions. No fixed plugin
counts; every venue is explained individually.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

PLUGIN_GROUP = "bt_api.plugins"
DEFAULT_BUNDLES_PATH = Path(__file__).resolve().parent.parent / "configs" / "exchange-bundles.toml"
CERTIFIED_CARDS_DIR = (
    Path(__file__).resolve().parent.parent / "docs" / "acceptance" / "exchange-cards"
)


@dataclass(frozen=True)
class VenueStatus:
    package: str
    plugin: str
    exchange: str
    min_version: str
    certification: str
    installed: bool
    version: str | None
    version_ok: bool
    entry_point: bool
    status: str


class PluginCatalog:
    """Reads bundle configuration and reports per-venue install status."""

    def __init__(self, bundles_path: str | Path = DEFAULT_BUNDLES_PATH) -> None:
        self._bundles_path = Path(bundles_path)
        self._config = self._load()

    def _load(self) -> dict[str, Any]:
        with self._bundles_path.open("rb") as handle:
            return tomllib.load(handle)

    def list_bundles(self) -> list[str]:
        bundles = self._config.get("bundles", {})
        return list(bundles.keys())

    def resolve_bundle(self, bundle_name: str) -> dict[str, Any]:
        bundles = self._config.get("bundles", {})
        if bundle_name not in bundles:
            raise ValueError(f"unknown bundle: {bundle_name}")
        bundle = bundles[bundle_name]
        venues = [self._resolve_venue(venue) for venue in bundle.get("venues", [])]
        return {
            "name": bundle_name,
            "description": bundle.get("description", ""),
            "venues": [self._venue_to_dict(venue) for venue in venues],
        }

    def _resolve_venue(self, venue: dict[str, Any]) -> VenueStatus:
        package = str(venue["package"])
        plugin = str(venue.get("plugin", ""))
        exchange = str(venue["exchange"])
        min_version = str(venue.get("min_version", ""))
        certification = str(venue.get("certification", "experimental"))

        installed, version = self._installed_version(package)
        version_ok = False
        if installed and version is not None and min_version:
            version_ok = self._version_satisfies(version, min_version)
        entry_point = self._has_entry_point(plugin)
        certified = (CERTIFIED_CARDS_DIR / f"{plugin}.md").exists()

        if not installed:
            status = "missing"
        elif certified:
            status = "certified"
        elif entry_point:
            status = "loadable"
        else:
            status = "installed"

        return VenueStatus(
            package=package,
            plugin=plugin,
            exchange=exchange,
            min_version=min_version,
            certification=certification,
            installed=installed,
            version=version,
            version_ok=version_ok,
            entry_point=entry_point,
            status=status,
        )

    @staticmethod
    def _installed_version(package: str) -> tuple[bool, str | None]:
        try:
            return True, metadata.version(package)
        except metadata.PackageNotFoundError:
            return False, None

    @staticmethod
    def _has_entry_point(plugin: str) -> bool:
        if not plugin:
            return False
        eps = metadata.entry_points()
        group_eps = eps.select(group=PLUGIN_GROUP) if hasattr(eps, "select") else []
        return any(ep.name == plugin for ep in group_eps)

    @staticmethod
    def _version_satisfies(version: str, min_version: str) -> bool:
        try:
            from packaging.version import Version

            return Version(version) >= Version(min_version)
        except Exception:
            return False

    @staticmethod
    def _venue_to_dict(venue: VenueStatus) -> dict[str, Any]:
        return {
            "package": venue.package,
            "plugin": venue.plugin,
            "exchange": venue.exchange,
            "min_version": venue.min_version,
            "certification": venue.certification,
            "installed": venue.installed,
            "version": venue.version,
            "version_ok": venue.version_ok,
            "entry_point": venue.entry_point,
            "status": venue.status,
        }
