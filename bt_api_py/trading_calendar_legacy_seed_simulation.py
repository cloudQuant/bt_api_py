"""Offline 2026 seed derived from the pinned old CTP schedule, never venue truth.

This optional simulation tool does not publish, configure trading, fetch data,
or authenticate sources or identities. The first open product day and the
boundary horizon remain unavailable; caller-declared mock absences are not
claims about real exchange sessions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_store import calendar_snapshot_digest
from bt_api_py.trading_calendar_update_simulation import VenueContent

_SOURCE = "MOCK.simulation_only"
_YEAR = 2026
_FIRST = date(2026, 1, 1)
_LAST = date(2026, 12, 31)
_MAX_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DAY_LIST_SHA256 = "95e5ef866af3b5cd9f6f01a3fd9a95f5bec4d88f9aa7457a1dc16a63d238536c"
_SOURCE_HASHES = {
    "trading_session_schedule.py": "461ca03afc865186c5d7134e1c09c64f966643a56a1eebdcd903c2954dd4765e",
    "futures_expected_calendar_provider.py": "cb5096a9a40ef5190cf54a845e8ef7619116bd31189a71fa34d4ed9e8604c19c",
    # Pinned byte digest of bt_api_ctp/calendar_snapshot.py as of 2026-11-18.
    # bt_api_ctp@87685760fd394857a4bad6fab722aacae1d0ef1a added the mock-only
    # interrupt_window action (close_day/cancel_window handling unchanged);
    # bt_api_ctp@f88777098136fde1d848afaa00bd897af36ff728 then added the
    # read-only owner_day_bounds() method, which does not change the seed
    # derivation or any close_day/cancel_window/interrupt_window semantics.
    "calendar_snapshot.py": "7d2dc17e5d00ac3bf1d6fabb894729c739efe5e5a9a5b17839878e7d4c0c5a33",
}
# Explicit copy of the old table. Case is part of the declaration.
_PROFILES = (
    ("SHFE", "rb", "23:00"),
    ("SHFE", "hc", "23:00"),
    ("SHFE", "bu", "23:00"),
    ("SHFE", "ru", "23:00"),
    ("SHFE", "fu", "23:00"),
    ("SHFE", "sp", "23:00"),
    ("SHFE", "cu", "01:00"),
    ("SHFE", "al", "01:00"),
    ("SHFE", "zn", "01:00"),
    ("SHFE", "pb", "01:00"),
    ("SHFE", "ni", "01:00"),
    ("SHFE", "sn", "01:00"),
    ("SHFE", "ss", "01:00"),
    ("SHFE", "au", "02:30"),
    ("SHFE", "ag", "02:30"),
    ("SHFE", "wr", None),
    ("INE", "sc", "02:30"),
    ("CZCE", "SA", "23:00"),
    ("CZCE", "FG", "23:00"),
    ("CZCE", "MA", "23:00"),
    ("CZCE", "TA", "23:00"),
    ("CZCE", "SR", "23:00"),
    ("CZCE", "CF", "23:00"),
    ("CZCE", "RM", "23:00"),
    ("CZCE", "OI", "23:00"),
    ("CZCE", "AP", None),
    ("CZCE", "CJ", None),
    ("DCE", "i", "23:00"),
    ("DCE", "j", "23:00"),
    ("DCE", "jm", "23:00"),
    ("DCE", "m", "23:00"),
    ("DCE", "y", "23:00"),
    ("DCE", "p", "23:00"),
    ("DCE", "a", "23:00"),
    ("DCE", "c", "23:00"),
    ("DCE", "l", "23:00"),
    ("DCE", "v", "23:00"),
    ("DCE", "pp", "23:00"),
    ("DCE", "eg", "23:00"),
    ("DCE", "jd", None),
    ("DCE", "lh", None),
)
_VENUES = ("SHFE", "INE", "CZCE", "DCE")


class LegacySeedError(ValueError):
    """Finite, value-free seed construction or verification error."""


@dataclass(frozen=True, slots=True)
class LegacySeedBundle:
    """One immutable serialized mock seed; its hash does not authenticate it."""

    payload: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class SavedSeedInspection:
    """Readback status only; no provenance or durability is established."""

    status: str
    source_authentication: str = "NOT_PROVEN"
    durability: str = "NOT_PROVEN"
    seed_rebuild_verification: str = "NOT_RUN"
    parent_path: str = "CALLER_TRUSTED_PARENT_SYMLINKS_MAY_BE_FOLLOWED"


def _no_follow_available() -> bool:
    value = getattr(os, "O_NOFOLLOW", None)
    return type(value) is int and value != 0


def _path_valid(path: Any) -> bool:
    return type(path) is str and Path(path).is_absolute() and ".." not in Path(path).parts


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _module_digest(module: Any) -> str:
    return _sha(Path(module.__file__).read_bytes())


def _legacy_inputs():
    from bt_api_ctp import (
        calendar_snapshot,
        futures_expected_calendar_provider,
        trading_session_schedule,
    )

    modules = (trading_session_schedule, futures_expected_calendar_provider, calendar_snapshot)
    if any(
        _module_digest(module) != _SOURCE_HASHES[Path(module.__file__).name] for module in modules
    ):
        raise LegacySeedError("LEGACY_SOURCE_SHA_MISMATCH")
    table = {
        (venue, product): end.strftime("%H:%M") if end is not None else None
        for (venue, product), end in trading_session_schedule._PRODUCT_NIGHT_END.items()
    }
    if (
        table != {(venue, product): end for venue, product, end in _PROFILES}
        or len(_PROFILES) != 41
    ):
        raise LegacySeedError("LEGACY_PROFILE_MISMATCH")
    days = {}
    for venue in _VENUES:
        selected = futures_expected_calendar_provider.expected_trading_days(venue, _YEAR)
        if (
            type(selected) is not tuple
            or len(selected) < 2
            or any(type(day) is not date or not _FIRST <= day <= _LAST for day in selected)
            or tuple(sorted(set(selected))) != selected
            or _sha(_canonical([day.isoformat() for day in selected])) != _DAY_LIST_SHA256
        ):
            raise LegacySeedError("LEGACY_DAY_SCOPE_UNAVAILABLE")
        days[venue] = selected
    for venue, product, end in _PROFILES:
        profile = trading_session_schedule.product_session_profile(product + "2601")
        if (
            profile is None
            or (profile[0], profile[1]) != (venue, product)
            or (profile[2].strftime("%H:%M") if profile[2] is not None else None) != end
        ):
            raise LegacySeedError("LEGACY_PROFILE_MISMATCH")
    return calendar_snapshot.CalendarSnapshot, days


def _window(name: str, phase: str, start: str, end: str, offset: int = 0):
    return {"id": name, "phase": phase, "start": start, "end": end, "end_day_offset": offset}


def build_seed(*, mode: str) -> LegacySeedBundle:
    """Build a single four-venue mock seed; no directories or network are used."""
    if type(mode) is not str or mode != "SIMULATION_ONLY":
        raise LegacySeedError("SIMULATION_MODE_REQUIRED")
    try:
        snapshot_factory, open_days = _legacy_inputs()
        all_days = tuple(_FIRST + timedelta(days=i) for i in range(365))
        venue_content = []
        mapping_declarations = []
        first_unknown = {}
        for venue in _VENUES:
            trading = open_days[venue]
            trading_set = set(trading)
            first_unknown[venue] = trading[0].isoformat()
            valid_from = trading[1]
            layers = {
                "sources": [
                    {"id": _SOURCE, "description": "fictional legacy-derived simulation only"}
                ],
                "trading_days": [
                    {
                        "venue": venue,
                        "day": day.isoformat(),
                        "status": "open" if day in trading_set else "closed",
                        "source_ids": [_SOURCE],
                    }
                    for day in all_days
                ],
                "trading_hours": [],
                "exceptions": [],
            }
            for profile_venue, product, night_end in _PROFILES:
                if profile_venue != venue:
                    continue
                mock_contract = f"mock_{venue.lower()}_{product.lower()}_future"
                mapping_declarations.append(
                    {
                        "venue": venue,
                        "kind": "future",
                        "product": product,
                        "mock_contract": mock_contract,
                        "legacy_instrument_id": product + "2601",
                        "identity_binding": "CALLER_DECLARED_NOT_PROVEN",
                        "night_end": night_end,
                    }
                )
                day_windows = [
                    _window("day-auction", "auction", "08:55", "09:00"),
                    _window("day-am-1", "continuous", "09:00", "10:15"),
                    _window("day-am-2", "continuous", "10:30", "11:30"),
                    _window("day-pm", "continuous", "13:30", "15:00"),
                ]
                night_windows = (
                    []
                    if night_end is None
                    else [
                        _window("night-auction", "auction", "20:55", "21:00"),
                        _window(
                            "night", "continuous", "21:00", night_end, int(night_end != "23:00")
                        ),
                    ]
                )
                night_mappings = []
                for i, owner in enumerate(trading[1:], start=1):
                    previous = trading[i - 1]
                    gap = (owner - previous).days
                    held = night_end is not None and all(
                        (previous + timedelta(days=n)).weekday() >= 5 for n in range(1, gap)
                    )
                    night_mappings.append(
                        {
                            "trading_day": owner.isoformat(),
                            "start_date": previous.isoformat() if held else None,
                            "source_ids": [_SOURCE],
                        }
                    )
                    if held:
                        layers["exceptions"].append(
                            {
                                "venue": venue,
                                "kind": "future",
                                "product": product,
                                "contract": mock_contract,
                                "trading_day": owner.isoformat(),
                                "action": "cancel_window",
                                "window_id": "day-auction",
                                "source_ids": [_SOURCE],
                            }
                        )
                layers["trading_hours"].append(
                    {
                        "venue": venue,
                        "kind": "future",
                        "product": product,
                        "contracts": [mock_contract],
                        "valid_from": valid_from.isoformat(),
                        "valid_to": _LAST.isoformat(),
                        "day_windows": day_windows,
                        "night_windows": night_windows,
                        "night_mappings": night_mappings,
                        "windows_complete": True,
                        "exceptions_complete": True,
                        "source_ids": [_SOURCE],
                    }
                )
            venue_content.append(VenueContent.from_layers(venue, _FIRST, _LAST, layers))
        result = compose_simulated(
            mode="SIMULATION_ONLY", venues=tuple(venue_content), snapshot_factory=snapshot_factory
        )
        if result.status != "READY_SIMULATED":
            raise LegacySeedError("SIMULATION_SEED_COMPOSITION_FAILED")
        content = json.loads(result.snapshot_bytes)
        version_sha = calendar_snapshot_digest(
            content,
            coverage_manifest_bytes=result.manifest_bytes,
            coverage_manifest_sha256=result.manifest_sha256,
            binding_bytes=result.binding_bytes,
            binding_sha256=result.binding_sha256,
        )
        owner_start = max(open_days[venue][1] for venue in _VENUES)
        start_local_day = owner_start + timedelta(days=1)
        end_local_day = _LAST - timedelta(days=2)
        payload = _canonical(
            {
                "schema": "bt_api.calendar-legacy-derived-seed/1",
                "metadata": {
                    "environment": "SIMULATION_ONLY",
                    "data_origin": "MOCK_LEGACY_DERIVED",
                    "source_authentication": "NOT_PROVEN",
                    "identity_authentication": "NOT_PROVEN",
                    "independent_venue_evidence": "NOT_PROVEN",
                    "trade_authorization": "NONE",
                    "source_id": _SOURCE,
                    "legacy_source_sha256": _SOURCE_HASHES,
                    "legacy_day_list_sha256": dict.fromkeys(_VENUES, _DAY_LIST_SHA256),
                    "legacy_instrument_identity": "CALLER_DECLARED_NOT_PROVEN",
                    "assumptions": [
                        "LEGACY_RULES_AND_HOLIDAYS_COPIED_NOT_AUTHENTICATED",
                        "NIGHT_ABSENCE_IS_SIMULATION_ASSUMPTION",
                        "NO_OUTSIDE_YEAR_OWNER_INFERENCE",
                    ],
                    "first_open_product_day_unknown": first_unknown,
                    "owner_start": owner_start.isoformat(),
                    "owner_end": _LAST.isoformat(),
                    "max_advance_days": 3,
                    "max_spill_days": 1,
                    "comparable_horizon_shanghai": {
                        "start": start_local_day.isoformat() + "T00:00:00+08:00",
                        "end": end_local_day.isoformat() + "T00:00:00+08:00",
                        "end_exclusive": True,
                    },
                    "outside_horizon": "UNKNOWN",
                    "venue_day_coverage": "DECLARED_UNVERIFIED",
                    "product_first_open_day": "UNKNOWN_NOT_CLOSED",
                    "store_metadata_binding": "JOIN_SEED_BY_VERSION_SHA256_METADATA_NOT_IN_STORE_SNAPSHOT",
                },
                "mappings": mapping_declarations,
                "snapshot": content,
                "manifest": json.loads(result.manifest_bytes),
                "binding": json.loads(result.binding_bytes),
                "snapshot_sha256": result.snapshot_sha256,
                "manifest_sha256": result.manifest_sha256,
                "binding_sha256": result.binding_sha256,
                "version_sha256": version_sha,
            }
        )
        if len(payload) > _MAX_BYTES:
            raise LegacySeedError("SIMULATION_SEED_SIZE_LIMIT")
        return LegacySeedBundle(payload, _sha(payload))
    except LegacySeedError:
        raise
    except Exception:
        raise LegacySeedError("SIMULATION_SEED_UNAVAILABLE") from None


def verify_seed(bundle: Any, *, mode: str) -> bool:
    """Rebuild from pinned code and compare exact canonical bytes, not just hashes."""
    if type(mode) is not str or mode != "SIMULATION_ONLY":
        return False
    if (
        type(bundle) is not LegacySeedBundle
        or type(bundle.payload) is not bytes
        or not 0 < len(bundle.payload) <= _MAX_BYTES
        or type(bundle.sha256) is not str
        or _sha(bundle.payload) != bundle.sha256
    ):
        return False
    try:
        value = json.loads(bundle.payload.decode("utf-8"))
        if _canonical(value) != bundle.payload:
            return False
        expected = build_seed(mode="SIMULATION_ONLY")
        return bundle == expected
    except Exception:
        return False


def _unknown_save(exc: BaseException) -> None:
    if isinstance(exc, Exception):
        raise LegacySeedError("SIMULATION_SEED_SAVE_UNKNOWN") from None
    # Preserve interrupts; a note communicates that the result must be inspected.
    with suppress(Exception):
        note = getattr(exc, "add_note", None)
        if callable(note):
            note(
                "SIMULATION_SEED_SAVE_UNKNOWN: inspect the original path and seed SHA; do not retry blindly"
            )
    raise exc


def save_seed(path: str, bundle: LegacySeedBundle, *, mode: str) -> None:
    """Exclusively save to a caller-trusted parent, without creating directories.

    Parent symlinks can be followed: only the final component has O_NOFOLLOW.
    This is not a hostile-path or Windows guarantee. An UNKNOWN result requires
    read-only inspection of the original path and SHA, not deletion or retry.
    Every failure after an open attempt, including EEXIST, is UNKNOWN.
    Successful fsync does not prove directory-entry or power-loss durability.
    """
    if not _path_valid(path) or not _no_follow_available() or not verify_seed(bundle, mode=mode):
        raise LegacySeedError("SIMULATION_SEED_SAVE_NOT_CREATED")
    owned_fd = None
    try:
        owned_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        stream = os.fdopen(owned_fd, "wb")
        owned_fd = None
        with stream:
            if stream.write(bundle.payload) != len(bundle.payload):
                raise OSError("short write")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as exc:
        if owned_fd is not None:
            with suppress(OSError):
                os.close(owned_fd)
        _unknown_save(exc)


def _read_saved_bytes(path: str) -> bytes:
    if not _path_valid(path) or not _no_follow_available():
        raise ValueError("invalid read")
    owned_fd = None
    try:
        owned_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        stream = os.fdopen(owned_fd, "rb")
        owned_fd = None
        with stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                raise ValueError("invalid file")
            payload = stream.read(_MAX_BYTES + 1)
            if len(payload) > _MAX_BYTES:
                raise ValueError("file exceeds limit")
            return payload
    finally:
        if owned_fd is not None:
            with suppress(OSError):
                os.close(owned_fd)


def inspect_saved_seed(path: str, *, expected_sha256: str, mode: str) -> SavedSeedInspection:
    """Compare bounded readback bytes only, without rebuilding the seed.

    Only the final component uses O_NOFOLLOW; caller-trusted parent symlinks may
    be followed. This is not an inode stability or power-loss durability proof.
    """
    if (
        type(expected_sha256) is not str
        or _SHA256.fullmatch(expected_sha256) is None
        or type(mode) is not str
        or mode != "SIMULATION_ONLY"
    ):
        return SavedSeedInspection("UNAVAILABLE")
    try:
        payload = _read_saved_bytes(path)
        return SavedSeedInspection(
            "HASH_MATCH" if _sha(payload) == expected_sha256 else "HASH_MISMATCH"
        )
    except Exception:
        return SavedSeedInspection("UNAVAILABLE")


def load_seed(path: str, *, expected_sha256: str, mode: str) -> LegacySeedBundle:
    """Read and rebuild-verify a seed against an explicit digest.

    Only the final component is protected against symlinks; caller-trusted
    parent symlinks may be followed. Windows support has not been verified.
    """
    if (
        type(expected_sha256) is not str
        or _SHA256.fullmatch(expected_sha256) is None
        or type(mode) is not str
        or mode != "SIMULATION_ONLY"
    ):
        raise LegacySeedError("SIMULATION_SEED_LOAD_UNAVAILABLE")
    try:
        bundle = LegacySeedBundle(_read_saved_bytes(path), expected_sha256)
        if not verify_seed(bundle, mode=mode):
            raise LegacySeedError("SIMULATION_SEED_LOAD_UNAVAILABLE")
        return bundle
    except Exception:
        raise LegacySeedError("SIMULATION_SEED_LOAD_UNAVAILABLE") from None
