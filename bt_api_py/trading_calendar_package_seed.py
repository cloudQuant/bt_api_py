"""Versioned, explicitly synthetic in-package trading-calendar seed.

D67-37 limits this period to six exchanges, futures and options, and the
current plus next year while real sources are incomplete. Every file, row and
API result therefore carries a ``SYNTHETIC_MOCK`` label. The seed is not
exchange truth, is not a daily feed, and grants no trading authorization.

The loader validates the index, manifest schema, per-file SHA-256, a combined
content digest and the declared coverage before materializing the four store
layers. Unknown versions and damaged packages fail with a finite code.

Installation is explicit and opt-in. ``install_package_seed`` never invents a
parallel writer: it builds a validated proposal and delegates to
``TradingCalendarStore.publish`` under the store's own writer lock, policy
evidence and CURRENT precondition. It only installs into a store whose review
policy is already initialized in ``auto`` mode and whose CURRENT is absent (or
already exactly this seed). It refuses any non-seed or different seed CURRENT,
never retries, and reports UNKNOWN/partial outcomes.

Installation also publishes a declared simulation-coverage manifest and its
binding so the installed snapshot is readable through the SIMULATION_ONLY
query path. The declaration is derived from the manifest coverage years and
venue list; it is a local, unverified claim that this synthetic snapshot covers
2026-01-01..2027-12-31 for each venue. Declaring coverage is not source
authentication and does not change the synthetic labels. Seed upgrade of a
populated store, Windows runtime behavior and any runtime binding remain out of
scope.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

INDEX_SCHEMA = "bt_api.calendar-package-seed.index/1"
MANIFEST_SCHEMA = "bt_api.calendar-package-seed.manifest/1"
LAYER_SCHEMA = "bt_api.calendar-package-seed.layer/1"
SEED_SCHEMA_VERSION = 1

COVERAGE_MANIFEST_SCHEMA = "bt_api.calendar-simulation-coverage/2"
BINDING_SCHEMA = "bt_api.calendar-simulation-binding/1"
COVERAGE_SEMANTICS = "DECLARED_INCLUSIVE"
COVERAGE_STATUS = "DECLARED_UNVERIFIED"

SEED_VENUES = ("CFFEX", "CZCE", "DCE", "GFEX", "INE", "SHFE")
SEED_KINDS = ("future", "option")
SEED_LAYERS = ("sources", "trading_days", "trading_hours", "exceptions")
DEFAULT_SEED_VERSION = "seed-2026-2027-v1"

SEED_PACKAGE = "bt_api_py"
SEED_DIRECTORY = "trading_calendar_seed"
DATA_ORIGIN = "SYNTHETIC_MOCK"
SEMANTICS = "STORE_INTEGRITY_ONLY"
VENUE_SEMANTICS = "VENUE_SEMANTICS_NOT_VERIFIED"
SEED_STATUS = "SEED_SYNTHETIC_UNVERIFIED"
DISCLAIMER = (
    "Synthetic mock calendar data (D67-37); not exchange truth; no trading "
    "authorization; no daily update. Every row and file keeps this label."
)

_INDEX_KEYS = {"schema", "default", "versions", "synthetic", "data_origin", "disclaimer"}
_MANIFEST_KEYS = {
    "schema",
    "schema_version",
    "seed_version",
    "generated_at",
    "synthetic",
    "data_origin",
    "disclaimer",
    "source_id",
    "coverage",
    "files",
    "content_digest",
    "semantics",
    "venue_semantics",
    "seed_status",
}
_COVERAGE_KEYS = {"venues", "kinds", "years", "layers"}
_LAYER_KEYS = {"schema", "layer", "synthetic", "data_origin", "disclaimer", "records", "rows"}
_FILE_KEYS = {"path", "sha256", "records"}
_WINDOW_KEYS = {"id", "phase", "start", "end", "end_day_offset"}
_VERSION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z", re.ASCII)
_SOURCE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z", re.ASCII)
_LAYER_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CLOCK = re.compile(r"[0-9]{2}:[0-9]{2}\Z", re.ASCII)
_SEED_SOURCE_PREFIX = f"{DATA_ORIGIN}.package_seed."

INSTALL_STAGED = "STAGED"
INSTALL_INSTALLED = "INSTALLED"
INSTALL_UNCHANGED = "UNCHANGED"
INSTALL_NOT_COMMITTED = "NOT_COMMITTED"
INSTALL_UNKNOWN = "UNKNOWN"


class PackageSeedError(ValueError):
    """The in-package seed is absent, unsupported, damaged or unlabeled."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SeedFile:
    layer: str
    path: str
    sha256: str
    records: int


@dataclass(frozen=True)
class PackageSeed:
    """Validated, labeled seed. No row is a venue fact."""

    seed_version: str
    schema_version: int
    generated_at: str
    source_id: str
    synthetic: bool
    data_origin: str
    disclaimer: str
    semantics: str
    venue_semantics: str
    seed_status: str
    coverage: Mapping[str, Any]
    files: tuple[SeedFile, ...]
    content_digest: str
    _content: Mapping[str, Any] = field(repr=False)
    _pairs: frozenset = field(repr=False)

    def materialize_content(self) -> dict[str, list]:
        """Return a detached copy of the four store layers."""
        return copy.deepcopy(dict(self._content))

    def covers_year(self, year: object) -> bool:
        return type(year) is int and year in self.coverage["years"]

    def covers(self, venue: object, kind: object) -> bool:
        return type(venue) is str and type(kind) is str and (venue, kind) in self._pairs

    def describe(self) -> dict[str, Any]:
        content_counts = {name: len(rows) for name, rows in self._content.items()}
        return {
            "seed_version": self.seed_version,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source_id": self.source_id,
            "synthetic": True,
            "data_origin": self.data_origin,
            "disclaimer": self.disclaimer,
            "semantics": self.semantics,
            "venue_semantics": self.venue_semantics,
            "seed_status": self.seed_status,
            "content_digest": self.content_digest,
            "coverage": {
                "venues": list(self.coverage["venues"]),
                "kinds": list(self.coverage["kinds"]),
                "years": list(self.coverage["years"]),
                "layers": list(self.coverage["layers"]),
            },
            "row_counts": content_counts,
        }


@dataclass(frozen=True)
class SeedPublication:
    """Validated staged material; still synthetic and unverified."""

    seed_version: str
    publication_id: str
    version_id: str
    content_sha256: str
    snapshot_sha256: str
    content: dict[str, list]
    coverage_manifest_bytes: bytes
    coverage_manifest_sha256: str
    binding_bytes: bytes
    binding_sha256: str
    synthetic: bool = True
    data_origin: str = DATA_ORIGIN
    disclaimer: str = DISCLAIMER
    semantics: str = SEMANTICS


@dataclass(frozen=True)
class SeedInstallResult:
    """Explicit install outcome; ``synthetic`` is never optional information."""

    status: str
    reason: str
    detail: str = ""
    seed_version: str = ""
    publication_id: str | None = None
    version_id: str | None = None
    content_sha256: str | None = None
    coverage_manifest_sha256: str | None = None
    binding_sha256: str | None = None
    store_root: str | None = None
    visibility: str | None = None
    verification: str | None = None
    post_commit: str | None = None
    fence: str | None = None
    coverage_status: str | None = None
    synthetic: bool = True
    data_origin: str = DATA_ORIGIN
    disclaimer: str = DISCLAIMER
    semantics: str = SEMANTICS
    venue_semantics: str = VENUE_SEMANTICS
    seed_status: str = SEED_STATUS

    @property
    def ok(self) -> bool:
        return self.status in (INSTALL_STAGED, INSTALL_INSTALLED, INSTALL_UNCHANGED)

    def describe(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "seed_version": self.seed_version,
            "publication_id": self.publication_id,
            "version_id": self.version_id,
            "content_sha256": self.content_sha256,
            "coverage_manifest_sha256": self.coverage_manifest_sha256,
            "binding_sha256": self.binding_sha256,
            "store_root": self.store_root,
            "visibility": self.visibility,
            "verification": self.verification,
            "post_commit": self.post_commit,
            "fence": self.fence,
            "coverage_status": self.coverage_status,
            "synthetic": self.synthetic,
            "data_origin": self.data_origin,
            "disclaimer": self.disclaimer,
            "semantics": self.semantics,
            "venue_semantics": self.venue_semantics,
            "seed_status": self.seed_status,
        }


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact(value: Any, keys: set[str], code: str) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise PackageSeedError(code)
    return value


def _label(value: dict) -> None:
    if value.get("synthetic") is not True:
        raise PackageSeedError("PACKAGE_SEED_NOT_SYNTHETIC")
    if value.get("data_origin") != DATA_ORIGIN:
        raise PackageSeedError("PACKAGE_SEED_NOT_SYNTHETIC")
    if type(value.get("disclaimer")) is not str or not value["disclaimer"].strip():
        raise PackageSeedError("PACKAGE_SEED_NOT_SYNTHETIC")


def _read_bytes(path: Any, *, missing: str, invalid: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise PackageSeedError(missing) from exc
    except OSError as exc:
        raise PackageSeedError(invalid) from exc


def _decode(data: bytes, code: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PackageSeedError(code) from exc


def _string(value: Any, code: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise PackageSeedError(code)
    return value


def _iso_day(value: Any, code: str) -> str:
    if type(value) is not str or len(value) != 10:
        raise PackageSeedError(code)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PackageSeedError(code) from exc
    if parsed.isoformat() != value:
        raise PackageSeedError(code)
    return value


def _days_for_year(year: int) -> list[str]:
    first, last = date(year, 1, 1), date(year + 1, 1, 1)
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days)]


def _validate_window(window: Any, *, night: bool) -> dict:
    value = _exact(window, _WINDOW_KEYS, "PACKAGE_SEED_FILE_INVALID")
    if type(value["id"]) is not str or not _VERSION.fullmatch(value["id"]):
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    if value["phase"] not in {"continuous", "auction"}:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    if type(value["end_day_offset"]) is not int or value["end_day_offset"] not in (0, 1):
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    if not night and value["end_day_offset"] != 0:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    if not all(
        type(value[name]) is str and _CLOCK.fullmatch(value[name]) for name in ("start", "end")
    ):
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    if value["end_day_offset"] == 0 and value["end"] <= value["start"]:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    if value["end_day_offset"] == 1 and value["end"] >= value["start"]:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    return value


def _validate_sources(rows: Any, source_id: str) -> list[dict]:
    if type(rows) is not list or not rows:
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    result = []
    for row in rows:
        value = _exact(row, {"id", "description"}, "PACKAGE_SEED_FILE_INVALID")
        _string(value["id"], "PACKAGE_SEED_FILE_INVALID")
        _string(value["description"], "PACKAGE_SEED_FILE_INVALID")
        result.append(value)
    if [row["id"] for row in result].count(source_id) != 1:
        raise PackageSeedError("PACKAGE_SEED_MANIFEST_INVALID")
    if len({row["id"] for row in result}) != len(result):
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    return result


def _validate_trading_days(rows: Any, *, venues: tuple, years: tuple) -> list[dict]:
    if type(rows) is not list:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    expected = {(venue, year) for venue in venues for year in years}
    seen = set()
    result = []
    for row in rows:
        value = _exact(
            row,
            {"venue", "year", "open_weekdays", "closed"},
            "PACKAGE_SEED_FILE_INVALID",
        )
        if value["venue"] not in venues or type(value["year"]) is not int:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        if value["year"] not in years:
            raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
        weekdays = value["open_weekdays"]
        if (
            type(weekdays) is not list
            or not weekdays
            or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
            or len(set(weekdays)) != len(weekdays)
        ):
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        closed = value["closed"]
        if type(closed) is not list:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        closed_set = set()
        for day in closed:
            _iso_day(day, "PACKAGE_SEED_FILE_INVALID")
            if day[:4] != str(value["year"]):
                raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
            closed_set.add(day)
        if len(closed_set) != len(closed):
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        key = (value["venue"], value["year"])
        if key in seen:
            raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
        seen.add(key)
        result.append(value)
    if seen != expected:
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    for value in result:
        statuses = set(_expanded_statuses(value).values())
        if statuses != {"open", "closed"}:
            raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    return result


def _expanded_statuses(row: dict) -> dict[str, str]:
    closed = set(row["closed"])
    return {
        day: "open"
        if date.fromisoformat(day).weekday() in row["open_weekdays"] and day not in closed
        else "closed"
        for day in _days_for_year(row["year"])
    }


def _validate_trading_hours(rows: Any, *, venues: tuple, kinds: tuple) -> list[dict]:
    if type(rows) is not list:
        raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
    expected = {(venue, kind) for venue in venues for kind in kinds}
    seen = set()
    result = []
    for row in rows:
        value = _exact(
            row,
            {"venue", "kind", "product", "contracts", "day_windows", "night_windows"},
            "PACKAGE_SEED_FILE_INVALID",
        )
        if value["venue"] not in venues or value["kind"] not in kinds:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        _string(value["product"], "PACKAGE_SEED_FILE_INVALID")
        contracts = value["contracts"]
        if (
            type(contracts) is not list
            or not contracts
            or any(type(item) is not str or not item for item in contracts)
            or len(set(contracts)) != len(contracts)
        ):
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        for name, night in (("day_windows", False), ("night_windows", True)):
            group = value[name]
            if type(group) is not list:
                raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
            for window in group:
                _validate_window(window, night=night)
        if not value["day_windows"]:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        ids = [window["id"] for window in value["day_windows"] + value["night_windows"]]
        if len(set(ids)) != len(ids):
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        key = (value["venue"], value["kind"])
        if key in seen:
            raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
        seen.add(key)
        result.append(value)
    if seen != expected:
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    return result


def _validate_exceptions(
    rows: Any,
    *,
    venues: tuple,
    kinds: tuple,
    hours: list[dict],
    statuses: dict[tuple[str, str], str],
) -> list[dict]:
    if type(rows) is not list or not rows:
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    hours_by_pair = {(row["venue"], row["kind"]): row for row in hours}
    window_ids = {
        (venue, kind): {window["id"] for window in row["day_windows"]}
        for (venue, kind), row in hours_by_pair.items()
    }
    products = {(venue, kind): row["product"] for (venue, kind), row in hours_by_pair.items()}
    seen = set()
    result = []
    for row in rows:
        value = _exact(
            row,
            {"venue", "kind", "product", "contract", "trading_day", "action", "window_id"},
            "PACKAGE_SEED_FILE_INVALID",
        )
        key = (value["venue"], value["kind"])
        if key not in hours_by_pair or value["product"] != products[key]:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        contract = value["contract"]
        if value["kind"] == "future":
            if type(contract) is not str or contract not in hours_by_pair[key]["contracts"]:
                raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        elif contract is not None:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        day = _iso_day(value["trading_day"], "PACKAGE_SEED_FILE_INVALID")
        if statuses.get((value["venue"], day)) != "open":
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        if value["action"] == "close_day":
            if value["window_id"] is not None:
                raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        elif value["action"] == "cancel_window":
            if value["window_id"] not in window_ids[key]:
                raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        else:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        seen.add(key)
        result.append(value)
    if seen != {(venue, kind) for venue in venues for kind in kinds}:
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    return result


def _validate_index(value: Any) -> dict:
    index = _exact(value, _INDEX_KEYS, "PACKAGE_SEED_INDEX_INVALID")
    if index["schema"] != INDEX_SCHEMA:
        raise PackageSeedError("PACKAGE_SEED_INDEX_INVALID")
    _label(index)
    versions = index["versions"]
    if (
        type(versions) is not list
        or not versions
        or any(type(item) is not str or not _VERSION.fullmatch(item) for item in versions)
        or len(set(versions)) != len(versions)
    ):
        raise PackageSeedError("PACKAGE_SEED_INDEX_INVALID")
    if index["default"] not in versions:
        raise PackageSeedError("PACKAGE_SEED_INDEX_INVALID")
    return index


def _validate_manifest(value: Any, *, version: str, index_versions: list[str]) -> dict:
    manifest = _exact(value, _MANIFEST_KEYS, "PACKAGE_SEED_MANIFEST_INVALID")
    if manifest["schema"] != MANIFEST_SCHEMA or manifest["schema_version"] != SEED_SCHEMA_VERSION:
        raise PackageSeedError("PACKAGE_SEED_SCHEMA_UNSUPPORTED")
    _label(manifest)
    if manifest["seed_version"] != version or version not in index_versions:
        raise PackageSeedError("PACKAGE_SEED_VERSION_UNKNOWN")
    if manifest["semantics"] != SEMANTICS:
        raise PackageSeedError("PACKAGE_SEED_MANIFEST_INVALID")
    if manifest["venue_semantics"] != VENUE_SEMANTICS or manifest["seed_status"] != SEED_STATUS:
        raise PackageSeedError("PACKAGE_SEED_MANIFEST_INVALID")
    source_id = manifest["source_id"]
    if (
        type(source_id) is not str
        or not source_id.startswith(_SEED_SOURCE_PREFIX)
        or not _SOURCE_ID.fullmatch(source_id)
    ):
        raise PackageSeedError("PACKAGE_SEED_MANIFEST_INVALID")
    try:
        datetime.strptime(
            _string(manifest["generated_at"], "PACKAGE_SEED_MANIFEST_INVALID"),
            "%Y-%m-%dT%H:%M:%SZ",
        )
    except ValueError as exc:
        raise PackageSeedError("PACKAGE_SEED_MANIFEST_INVALID") from exc
    coverage = _exact(manifest["coverage"], _COVERAGE_KEYS, "PACKAGE_SEED_COVERAGE_INCOMPLETE")
    venues = coverage["venues"]
    if (
        type(venues) is not list
        or len(venues) != len(SEED_VENUES)
        or set(venues) != set(SEED_VENUES)
    ):
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    kinds = coverage["kinds"]
    if type(kinds) is not list or len(kinds) != len(SEED_KINDS) or set(kinds) != set(SEED_KINDS):
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    years = coverage["years"]
    if (
        type(years) is not list
        or len(years) != 2
        or any(type(year) is not int or year < 2000 for year in years)
        or years[1] != years[0] + 1
    ):
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    layers = coverage["layers"]
    if (
        type(layers) is not list
        or len(layers) != len(SEED_LAYERS)
        or set(layers) != set(SEED_LAYERS)
    ):
        raise PackageSeedError("PACKAGE_SEED_COVERAGE_INCOMPLETE")
    files = _exact(manifest["files"], set(SEED_LAYERS), "PACKAGE_SEED_MANIFEST_INVALID")
    normalized = {}
    for name, entry in files.items():
        value = _exact(entry, _FILE_KEYS, "PACKAGE_SEED_MANIFEST_INVALID")
        if (
            type(value["path"]) is not str
            or not _LAYER_FILE.fullmatch(value["path"])
            or type(value["records"]) is not int
            or value["records"] < 0
            or type(value["sha256"]) is not str
            or not _DIGEST.fullmatch(value["sha256"])
        ):
            raise PackageSeedError("PACKAGE_SEED_MANIFEST_INVALID")
        normalized[name] = dict(value)
    if manifest["content_digest"] != _sha(_canonical({"files": normalized})):
        raise PackageSeedError("PACKAGE_SEED_DIGEST_MISMATCH")
    manifest["coverage"] = {
        "venues": tuple(sorted(venues)),
        "kinds": tuple(kinds),
        "years": tuple(years),
        "layers": tuple(layers),
    }
    manifest["files"] = normalized
    return manifest


def _materialize(manifest: dict, layers: dict[str, list[dict]]) -> dict[str, list]:
    venues = manifest["coverage"]["venues"]
    years = manifest["coverage"]["years"]
    source_id = manifest["source_id"]
    source_ids = [source_id]
    days = []
    statuses: dict[tuple[str, str], str] = {}
    open_days: dict[str, list[str]] = {venue: [] for venue in venues}
    for row in layers["trading_days"]:
        for day, status in _expanded_statuses(row).items():
            days.append(
                {
                    "venue": row["venue"],
                    "day": day,
                    "status": status,
                    "source_ids": list(source_ids),
                }
            )
            statuses[(row["venue"], day)] = status
            if status == "open":
                open_days[row["venue"]].append(day)
    hours = []
    for row in layers["trading_hours"]:
        # Every open product-day needs an explicit night mapping and a null
        # start_date means "no night session". A day-only row therefore lists
        # each open day with a null start date, while a night row chains the
        # previous open day as the night-session start.
        previous = None
        mappings = []
        for day in open_days[row["venue"]]:
            start_date = previous if row["night_windows"] else None
            mappings.append(
                {"trading_day": day, "start_date": start_date, "source_ids": list(source_ids)}
            )
            previous = day
        hours.append(
            {
                "venue": row["venue"],
                "kind": row["kind"],
                "product": row["product"],
                "contracts": list(row["contracts"]),
                "valid_from": f"{years[0]}-01-01",
                "valid_to": f"{years[-1]}-12-31",
                "day_windows": copy.deepcopy(row["day_windows"]),
                "night_windows": copy.deepcopy(row["night_windows"]),
                "night_mappings": mappings,
                # The seed declares a complete synthetic rule set for its own
                # coverage; this is a declaration-completeness flag and never
                # exchange truth or trading authorization.
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": list(source_ids),
            }
        )
    exceptions = [
        {**copy.deepcopy(row), "source_ids": list(source_ids)} for row in layers["exceptions"]
    ]
    return {
        "sources": copy.deepcopy(layers["sources"]),
        "trading_days": days,
        "trading_hours": hours,
        "exceptions": exceptions,
    }


def load_package_seed_from_directory(directory: Any, *, version: str | None = None) -> PackageSeed:
    """Load and validate a seed tree exported on disk (for tests and inspection)."""
    root = Path(directory) if not hasattr(directory, "joinpath") else directory
    index_path = root.joinpath("index.json")
    if not index_path.is_file():
        raise PackageSeedError("PACKAGE_SEED_INDEX_MISSING")
    index = _validate_index(
        _decode(
            _read_bytes(
                index_path,
                missing="PACKAGE_SEED_INDEX_MISSING",
                invalid="PACKAGE_SEED_INDEX_INVALID",
            ),
            "PACKAGE_SEED_INDEX_INVALID",
        )
    )
    selected = index["default"] if version is None else version
    if type(selected) is not str or selected not in index["versions"]:
        raise PackageSeedError("PACKAGE_SEED_VERSION_UNKNOWN")
    version_root = root.joinpath(selected)
    manifest_path = version_root.joinpath("manifest.json")
    if not manifest_path.is_file():
        raise PackageSeedError("PACKAGE_SEED_MANIFEST_MISSING")
    manifest = _validate_manifest(
        _decode(
            _read_bytes(
                manifest_path,
                missing="PACKAGE_SEED_MANIFEST_MISSING",
                invalid="PACKAGE_SEED_MANIFEST_INVALID",
            ),
            "PACKAGE_SEED_MANIFEST_INVALID",
        ),
        version=selected,
        index_versions=index["versions"],
    )
    layers: dict[str, list[dict]] = {}
    for name in SEED_LAYERS:
        entry = manifest["files"][name]
        path = version_root.joinpath(entry["path"])
        if not path.is_file():
            raise PackageSeedError("PACKAGE_SEED_FILE_MISSING")
        data = _read_bytes(
            path, missing="PACKAGE_SEED_FILE_MISSING", invalid="PACKAGE_SEED_FILE_INVALID"
        )
        if _sha(data) != entry["sha256"]:
            raise PackageSeedError("PACKAGE_SEED_DIGEST_MISMATCH")
        value = _exact(
            _decode(data, "PACKAGE_SEED_FILE_INVALID"), _LAYER_KEYS, "PACKAGE_SEED_FILE_INVALID"
        )
        if value["schema"] != LAYER_SCHEMA or value["layer"] != name:
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        _label(value)
        if type(value["rows"]) is not list or value["records"] != len(value["rows"]):
            raise PackageSeedError("PACKAGE_SEED_FILE_INVALID")
        layers[name] = value["rows"]

    venues, kinds = manifest["coverage"]["venues"], manifest["coverage"]["kinds"]
    years = manifest["coverage"]["years"]
    sources = _validate_sources(layers["sources"], manifest["source_id"])
    trading_days = _validate_trading_days(layers["trading_days"], venues=venues, years=years)
    trading_hours = _validate_trading_hours(layers["trading_hours"], venues=venues, kinds=kinds)
    statuses = {}
    for row in trading_days:
        for day, status in _expanded_statuses(row).items():
            statuses[(row["venue"], day)] = status
    exceptions = _validate_exceptions(
        layers["exceptions"], venues=venues, kinds=kinds, hours=trading_hours, statuses=statuses
    )
    validated = {
        "sources": sources,
        "trading_days": trading_days,
        "trading_hours": trading_hours,
        "exceptions": exceptions,
    }
    content = _materialize(manifest, validated)
    files = tuple(
        SeedFile(
            name,
            manifest["files"][name]["path"],
            manifest["files"][name]["sha256"],
            manifest["files"][name]["records"],
        )
        for name in SEED_LAYERS
    )
    pairs = frozenset((row["venue"], row["kind"]) for row in trading_hours)
    return PackageSeed(
        seed_version=manifest["seed_version"],
        schema_version=manifest["schema_version"],
        generated_at=manifest["generated_at"],
        source_id=manifest["source_id"],
        synthetic=True,
        data_origin=manifest["data_origin"],
        disclaimer=manifest["disclaimer"],
        semantics=manifest["semantics"],
        venue_semantics=manifest["venue_semantics"],
        seed_status=manifest["seed_status"],
        coverage={
            "venues": venues,
            "kinds": kinds,
            "years": years,
            "layers": tuple(SEED_LAYERS),
        },
        files=files,
        content_digest=manifest["content_digest"],
        _content=content,
        _pairs=pairs,
    )


def load_package_seed(version: str | None = None, *, package: str = SEED_PACKAGE) -> PackageSeed:
    """Load the seed shipped inside the installed package."""
    root = resources.files(package).joinpath(SEED_DIRECTORY)
    return load_package_seed_from_directory(root, version=version)


def _coverage_declarations(coverage: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build the declared inclusive interval per venue from the manifest years.

    Venues are emitted in strictly ascending order, as the store and binding
    contracts require. The declaration is a local synthetic claim only.
    """
    start = f"{coverage['years'][0]}-01-01"
    end = f"{coverage['years'][-1]}-12-31"
    return [
        {
            "venue": venue,
            "coverage_start": start,
            "coverage_end": end,
            "semantics": COVERAGE_SEMANTICS,
        }
        for venue in sorted(coverage["venues"])
    ]


def _coverage_artifacts(seed: PackageSeed, content_sha256: str) -> tuple[bytes, str, bytes, str]:
    """Build canonical coverage manifest and binding bytes for one snapshot."""
    manifest = _canonical(
        {
            "schema": COVERAGE_MANIFEST_SCHEMA,
            "snapshot_sha256": content_sha256,
            "coverage": _coverage_declarations(seed.coverage),
            "data_origin": DATA_ORIGIN,
        }
    )
    manifest_sha256 = _sha(manifest)
    binding = _canonical(
        {
            "schema": BINDING_SCHEMA,
            "snapshot_sha256": content_sha256,
            "manifest_sha256": manifest_sha256,
        }
    )
    return manifest, manifest_sha256, binding, _sha(binding)


def prepare_package_seed_publication(
    seed: PackageSeed | None = None, *, version: str | None = None
) -> SeedPublication:
    """Build validated, deterministic material without touching a store."""
    selected = load_package_seed(version) if seed is None else seed
    if type(selected) is not PackageSeed:
        raise TypeError("seed must be a PackageSeed")
    content = selected.materialize_content()
    content_bytes = _canonical(content)
    content_sha256 = _sha(content_bytes)
    manifest, manifest_sha256, binding, binding_sha256 = _coverage_artifacts(
        selected, content_sha256
    )
    # Reuse the store's own contract so the staged digest is exactly the
    # digest of the version the installer will publish.
    snapshot_sha256 = _store_module().calendar_snapshot_digest(
        content,
        coverage_manifest_bytes=manifest,
        coverage_manifest_sha256=manifest_sha256,
        binding_bytes=binding,
        binding_sha256=binding_sha256,
    )
    return SeedPublication(
        seed_version=selected.seed_version,
        publication_id=selected.seed_version,
        version_id=selected.seed_version,
        content_sha256=content_sha256,
        snapshot_sha256=snapshot_sha256,
        content=content,
        coverage_manifest_bytes=manifest,
        coverage_manifest_sha256=manifest_sha256,
        binding_bytes=binding,
        binding_sha256=binding_sha256,
        disclaimer=selected.disclaimer,
    )


# ---------------------------------------------------------------------------
# Explicit installation through the managed store
# ---------------------------------------------------------------------------


def _result(
    publication: SeedPublication, status: str, reason: str, **extra: Any
) -> SeedInstallResult:
    values = {
        "content_sha256": publication.content_sha256,
        "coverage_manifest_sha256": publication.coverage_manifest_sha256,
        "binding_sha256": publication.binding_sha256,
        "synthetic": True,
        "data_origin": publication.data_origin,
        "disclaimer": publication.disclaimer,
        "semantics": publication.semantics,
    }
    values.update(extra)
    return SeedInstallResult(
        status=status,
        reason=reason,
        seed_version=publication.seed_version,
        **values,
    )


def _validated_root(value: Any) -> tuple[str, Path] | None:
    try:
        raw = os.fspath(value)
    except TypeError:
        return None
    if type(raw) is not str or not raw or raw != raw.strip() or "\x00" in raw:
        return None
    # Home, environment variables and relative names are never expanded here.
    if raw.startswith("~"):
        return None
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        return None
    return raw, path


def _is_seed_content(content: Any) -> bool:
    try:
        sources = content["sources"]
        return any(
            type(row) is dict
            and type(row.get("id")) is str
            and row["id"].startswith(_SEED_SOURCE_PREFIX)
            for row in sources
        )
    except (KeyError, TypeError):
        return False


def _store_module():
    from bt_api_py import trading_calendar_store

    return trading_calendar_store


def install_package_seed(
    store_root: Any,
    *,
    version: str | None = None,
    dry_run: bool = False,
) -> SeedInstallResult:
    """Install the synthetic seed into an already-initialized auto store.

    Explicit opt-in only. Preconditions: an existing absolute local directory,
    an initialized ``auto`` review policy, and either no CURRENT or a CURRENT
    whose content is exactly this seed. A different seed or non-seed CURRENT is
    never replaced. Publication is delegated to the managed store under its own
    writer lock, policy evidence and CURRENT precondition; the result is never
    retried. Ambiguous publication or verification is reported as UNKNOWN.
    """
    if type(dry_run) is not bool:
        raise TypeError("dry_run must be boolean")
    seed = load_package_seed(version)
    publication = prepare_package_seed_publication(seed)
    validated = _validated_root(store_root)
    if validated is None:
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            "SEED_ROOT_INVALID",
            detail="store root must be an existing absolute directory without home expansion",
            store_root=None,
        )
    raw_root, root = validated
    if dry_run:
        return _result(
            publication,
            INSTALL_STAGED,
            "SEED_STAGED",
            publication_id=publication.publication_id,
            version_id=publication.version_id,
            store_root=raw_root,
            visibility="NOT_ATTEMPTED",
            post_commit="NOT_APPLICABLE",
        )
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            "SEED_ROOT_INVALID",
            detail="store root does not exist",
            store_root=raw_root,
        )
    except OSError:
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            "SEED_ROOT_INVALID",
            detail="store root cannot be inspected",
            store_root=raw_root,
        )
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            "SEED_ROOT_INVALID",
            detail="store root must be a real directory",
            store_root=raw_root,
        )
    policy_path = os.path.join(raw_root, "REVIEW_POLICY.json")
    marker_path = os.path.join(raw_root, "REVIEW_POLICY_INITIALIZED.json")
    current_path = os.path.join(raw_root, "CURRENT.json")
    policy_present, marker_present = os.path.lexists(policy_path), os.path.lexists(marker_path)
    if not policy_present and not marker_present:
        if os.path.lexists(current_path):
            return _result(
                publication,
                INSTALL_UNKNOWN,
                "STORE_STATE_UNKNOWN",
                detail="CURRENT exists without an initialized review policy",
                store_root=raw_root,
            )
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            "STORE_NOT_INITIALIZED",
            detail="initialize the store review policy explicitly before installing the seed",
            store_root=raw_root,
        )
    if policy_present != marker_present:
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "STORE_STATE_UNKNOWN",
            detail="review policy state is incomplete",
            store_root=raw_root,
        )

    store_api = _store_module()
    store = store_api.TradingCalendarStore(root)
    try:
        policy = store.read_review_policy()
    except store_api.CalendarStoreError as exc:
        text = str(exc)
        reason = "PENDING_OPERATION" if "pending" in text else "STORE_STATE_UNKNOWN"
        status = INSTALL_NOT_COMMITTED if reason == "PENDING_OPERATION" else INSTALL_UNKNOWN
        return _result(publication, status, reason, detail=text, store_root=raw_root)
    except (OSError, ValueError) as exc:
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "STORE_STATE_UNKNOWN",
            detail=str(exc),
            store_root=raw_root,
        )
    if policy.mode == "manual":
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            "MANUAL_REVIEW_REQUIRED",
            detail="manual review stores do not accept an unreviewed package seed",
            store_root=raw_root,
        )

    current = None
    if os.path.lexists(current_path):
        try:
            current = store.read_current()
        except (store_api.CalendarStoreError, OSError, ValueError) as exc:
            return _result(
                publication,
                INSTALL_UNKNOWN,
                "STORE_STATE_UNKNOWN",
                detail="existing CURRENT cannot be read safely: " + str(exc),
                store_root=raw_root,
            )
        if current.content_sha256 == publication.content_sha256:
            return _result(
                publication,
                INSTALL_UNCHANGED,
                "ALREADY_CURRENT",
                publication_id=current.publication_id,
                version_id=current.version_id,
                content_sha256=current.content_sha256,
                store_root=raw_root,
                visibility="COMMITTED",
                verification="VISIBLE",
                post_commit="COMPLETE",
                coverage_status=current.coverage_status,
            )
        reason = (
            "SEED_CONTENT_CONFLICT" if _is_seed_content(current.content) else "CURRENT_NOT_SEED"
        )
        return _result(
            publication,
            INSTALL_NOT_COMMITTED,
            reason,
            detail="existing CURRENT differs; the installer never replaces it",
            store_root=raw_root,
            visibility="COMMITTED",
            verification="VISIBLE",
            coverage_status=current.coverage_status,
        )

    difference = {
        "schema": "bt_api.calendar-package-seed.install-difference/1",
        "operation": "INITIAL_SEED_INSTALL",
        "seed_version": publication.seed_version,
        "synthetic": True,
        "data_origin": publication.data_origin,
        "disclaimer": publication.disclaimer,
        "content_sha256": publication.content_sha256,
        "coverage_manifest_sha256": publication.coverage_manifest_sha256,
        "binding_sha256": publication.binding_sha256,
        "coverage_status": COVERAGE_STATUS,
    }
    claim = store_api.PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        publication.snapshot_sha256,
        difference,
        _sha(_canonical(difference)),
    )
    try:
        result = store.publish(
            publication.content,
            publication_id=publication.publication_id,
            version_id=publication.version_id,
            coverage_manifest_bytes=publication.coverage_manifest_bytes,
            coverage_manifest_sha256=publication.coverage_manifest_sha256,
            binding_bytes=publication.binding_bytes,
            binding_sha256=publication.binding_sha256,
            expected_current=store_api.CurrentPrecondition.absent(),
            policy_publication=claim,
        )
    except (store_api.CalendarStoreError, OSError, ValueError) as exc:
        return _result(
            publication, INSTALL_UNKNOWN, "PUBLISH_UNKNOWN", detail=str(exc), store_root=raw_root
        )
    if result.visibility == "UNCHANGED":
        try:
            current_status = store.read_current().coverage_status
        except (store_api.CalendarStoreError, OSError, ValueError):
            current_status = None
        return _result(
            publication,
            INSTALL_UNCHANGED,
            "ALREADY_CURRENT",
            publication_id=result.publication_id,
            version_id=result.version_id,
            store_root=raw_root,
            visibility=result.visibility,
            post_commit=result.post_commit,
            coverage_status=current_status,
        )
    if result.visibility != "COMMITTED":
        if result.visibility == "NOT_COMMITTED":
            return _result(
                publication,
                INSTALL_NOT_COMMITTED,
                "PUBLISH_REJECTED",
                detail=result.detail,
                publication_id=result.publication_id,
                version_id=result.version_id,
                store_root=raw_root,
                visibility=result.visibility,
                verification=result.durability,
                post_commit=result.post_commit,
                fence=result.fence,
            )
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "PUBLISH_UNKNOWN",
            detail=result.detail,
            publication_id=result.publication_id,
            version_id=result.version_id,
            store_root=raw_root,
            visibility=result.visibility,
            post_commit=result.post_commit,
            fence=result.fence,
        )
    try:
        verification = store.verify_publication(publication.publication_id)
    except (store_api.CalendarStoreError, OSError, ValueError) as exc:
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "POST_COMMIT_UNCONFIRMED",
            detail=str(exc),
            publication_id=publication.publication_id,
            version_id=publication.version_id,
            store_root=raw_root,
            visibility=result.visibility,
            verification="UNKNOWN",
            post_commit=result.post_commit,
            fence=result.fence,
        )
    if verification != "VISIBLE":
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "POST_COMMIT_UNCONFIRMED",
            detail="published version is not visible as CURRENT",
            publication_id=publication.publication_id,
            version_id=publication.version_id,
            store_root=raw_root,
            visibility=result.visibility,
            verification=verification,
            post_commit=result.post_commit,
            fence=result.fence,
        )
    try:
        installed = store.read_current()
    except (store_api.CalendarStoreError, OSError, ValueError) as exc:
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "POST_COMMIT_UNCONFIRMED",
            detail="post-publish readback failed: " + str(exc),
            publication_id=publication.publication_id,
            version_id=publication.version_id,
            store_root=raw_root,
            visibility=result.visibility,
            verification=verification,
            post_commit=result.post_commit,
            fence=result.fence,
        )
    if installed.content_sha256 != publication.content_sha256:
        return _result(
            publication,
            INSTALL_UNKNOWN,
            "POST_COMMIT_UNCONFIRMED",
            detail="post-publish content digest differs from the staged seed",
            publication_id=publication.publication_id,
            version_id=publication.version_id,
            store_root=raw_root,
            visibility=result.visibility,
            verification=verification,
            post_commit=result.post_commit,
            fence=result.fence,
        )
    return _result(
        publication,
        INSTALL_INSTALLED,
        "SEED_INSTALLED",
        publication_id=publication.publication_id,
        version_id=publication.version_id,
        store_root=raw_root,
        visibility=result.visibility,
        verification=verification,
        post_commit=result.post_commit,
        fence=result.fence,
        coverage_status=installed.coverage_status,
    )
