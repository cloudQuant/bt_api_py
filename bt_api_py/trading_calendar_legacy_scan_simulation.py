"""Opt-in POSIX offline migration scan; all data are MOCK_LEGACY_DERIVED.

Caller supplies a trusted parent directory. No network, account, SMTP or
production configuration is used. Only final path components are protected
against symlinks. Local hashes are not authentication or crash durability.
Run init, then explicitly run; importing this module starts nothing.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import stat
import tempfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bt_api_py import trading_calendar_legacy_seed_simulation as seed
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from bt_api_py.trading_calendar_store import (
    CalendarReadBudget,
    CurrentPrecondition,
    PolicyPublication,
    TradingCalendarStore,
)

_ZONE = ZoneInfo("Asia/Shanghai")
_START = datetime(2026, 1, 1, tzinfo=_ZONE).astimezone(UTC)
_END = datetime(2027, 1, 1, tzinfo=_ZONE).astimezone(UTC)
_SAFE_START = datetime(2026, 1, 7, tzinfo=_ZONE).astimezone(UTC)
_SAFE_END = datetime(2026, 12, 29, tzinfo=_ZONE).astimezone(UTC)
_STEP = timedelta(minutes=5)
_POINTS = 105120
_DIMENSIONS = ("phase", "owner", "next", "window")
_MAX_FILE = 8 * 1024 * 1024
_BUDGET = CalendarReadBudget(8 * 1024 * 1024, 64 * 1024 * 1024, 2048)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)


class SimulationScanError(RuntimeError):
    """Finite local scan error; no path or original exception is disclosed."""


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: str) -> str:
    return _sha(Path(path).read_bytes())


def _root(root: Any) -> str:
    if type(root) is not str or not Path(root).is_absolute() or ".." in Path(root).parts:
        raise SimulationScanError("SCAN_INPUT_INVALID")
    if not all(hasattr(os, n) and getattr(os, n) for n in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise SimulationScanError("SCAN_POSIX_UNAVAILABLE")
    return root


def _write_new(dirfd: int, name: str, payload: bytes) -> None:
    if type(payload) is not bytes or len(payload) > _MAX_FILE:
        raise SimulationScanError("SCAN_PAYLOAD_INVALID")
    fd = None
    try:
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd
        )
        stream = os.fdopen(fd, "wb")
        fd = None
        with stream:
            if stream.write(payload) != len(payload):
                raise OSError("short write")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException as exc:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        if isinstance(exc, Exception):
            raise SimulationScanError("SCAN_WRITE_UNKNOWN") from None
        note = getattr(exc, "add_note", None)
        if callable(note):
            note("SCAN_WRITE_UNKNOWN: inspect original run files; do not retry or remove blindly")
        raise


def _read_file(dirfd: int, name: str) -> bytes:
    fd = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=dirfd)
        stream = os.fdopen(fd, "rb")
        fd = None
        with stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE:
                raise ValueError("invalid file")
            payload = stream.read(_MAX_FILE + 1)
            if len(payload) > _MAX_FILE:
                raise ValueError("file too large")
            return payload
    finally:
        if fd is not None:
            os.close(fd)


def _read_object(dirfd: int, name: str) -> dict:
    payload = _read_file(dirfd, name)
    obj = json.loads(payload)
    if type(obj) is not dict or _json(obj) != payload:
        raise ValueError("invalid document")
    return obj


@contextmanager
def _locked(root: str):
    directory = lock = None
    try:
        directory = os.open(_root(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        lock = os.open(
            ".lock", os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=directory
        )
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ValueError("invalid lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield directory
    except BlockingIOError:
        raise SimulationScanError("SCAN_BUSY") from None
    except OSError:
        raise SimulationScanError("SCAN_LOCK_UNAVAILABLE") from None
    finally:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)


def _fingerprints() -> dict[str, str]:
    from bt_api_base import (
        account_protocol,
        execution_query_protocol,
        futures_expected_calendar_protocol,
        futures_market_protocol,
        market_protocol,
        query_protocol,
        service_protocol,
        trading_session_protocol,
    )
    from bt_api_ctp import (
        calendar_snapshot,
        futures_expected_calendar_provider,
        simulation_calendar_ports,
        trading_session_schedule,
    )

    from bt_api_py import (
        trading_calendar_binding,
        trading_calendar_composite,
        trading_calendar_migration_barrier,
        trading_calendar_store,
        trading_calendar_update_simulation,
    )

    modules = (
        seed,
        trading_calendar_binding,
        trading_calendar_composite,
        trading_calendar_migration_barrier,
        trading_calendar_store,
        trading_calendar_update_simulation,
        account_protocol,
        execution_query_protocol,
        futures_expected_calendar_protocol,
        futures_market_protocol,
        market_protocol,
        query_protocol,
        service_protocol,
        trading_session_protocol,
        calendar_snapshot,
        futures_expected_calendar_provider,
        simulation_calendar_ports,
        trading_session_schedule,
    )
    return {module.__name__: _digest_file(module.__file__) for module in modules} | {
        "scanner": _digest_file(__file__),
    }


def _timezone_grid_digest() -> str:
    """Bind the UTC offset at each point of the entire annual scan grid."""
    digest = hashlib.sha256()
    for index in range(_POINTS):
        instant = _START + index * _STEP
        offset = instant.astimezone(_ZONE).utcoffset()
        if offset is None:
            raise SimulationScanError("SCAN_TIMEZONE_UNAVAILABLE")
        digest.update(f"{offset.total_seconds():.0f}\n".encode("ascii"))
    return digest.hexdigest()


def _assert_runtime(manifest: dict) -> None:
    try:
        if (
            manifest["schema"] != "bt_api.legacy-scan-run/2"
            or manifest["code_sha256"] != _fingerprints()
            or manifest["timezone_grid_sha256"] != _timezone_grid_digest()
            or manifest["python"] != list(__import__("sys").version_info[:3])
        ):
            raise ValueError("runtime mismatch")
    except Exception:
        raise SimulationScanError("SCAN_IDENTITY_UNAVAILABLE") from None


def _manifest(
    bundle: seed.LegacySeedBundle,
    *,
    start: datetime,
    end: datetime,
    aliases: tuple[str, ...],
    test: bool,
) -> dict:
    value = json.loads(bundle.payload)
    selected = [item for item in value["mappings"] if item["legacy_instrument_id"] in aliases]
    if len(selected) != len(aliases) or len(set(aliases)) != len(aliases):
        raise SimulationScanError("SCAN_INPUT_INVALID")
    count = int((end - start) / _STEP)
    if not test and (start != _START or end != _END or count != _POINTS or len(selected) != 41):
        raise SimulationScanError("SCAN_INPUT_INVALID")
    return {
        "schema": "bt_api.legacy-scan-run/2",
        "scope": "TEST_ONLY_NOT_ANNUAL" if test else "ANNUAL_2026",
        "environment": "SIMULATION_ONLY",
        "source": "MOCK_LEGACY_DERIVED",
        "source_authentication": "NOT_PROVEN",
        "identity_authentication": "NOT_PROVEN",
        "trade_authorization": "NONE",
        "independent_venue_evidence": "NOT_PROVEN",
        "parent_directory": "CALLER_TRUSTED_SYMLINKS_MAY_BE_FOLLOWED",
        "durability": "NOT_PROVEN",
        "windows": "WINDOWS_EMPTY_IS_UNKNOWN",
        "seed_sha256": bundle.sha256,
        "content_sha256": value["snapshot_sha256"],
        "version_sha256": value["version_sha256"],
        "code_sha256": _fingerprints(),
        "python": list(__import__("sys").version_info[:3]),
        "timezone": "Asia/Shanghai",
        "timezone_grid_sha256": _timezone_grid_digest(),
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "step_minutes": 5,
        "rle_encoding": {
            "run": "[start_index,length,code]",
            "fields": ["phase", "owner", "next", "window", "legacy_state_windows"],
            "comparison_codes": {"E": "VALUE_EQUAL", "M": "MISMATCH", "U": "UNKNOWN"},
            "legacy_codes": {"C": "CONFLICT", "N": "NO_POSITIVE_CONFLICT", "U": "UNKNOWN"},
            "timestamp": "start_utc_plus_start_index_times_five_minutes",
        },
        "points_per_shard": count,
        "expected_shards": len(selected),
        "expected_points": count * len(selected),
        "horizon": value["metadata"]["comparable_horizon_shanghai"],
        "owner_start": value["metadata"]["owner_start"],
        "owner_end": value["metadata"]["owner_end"],
        "max_advance_days": value["metadata"]["max_advance_days"],
        "max_spill_days": value["metadata"]["max_spill_days"],
        "first_open_product_day_unknown": value["metadata"]["first_open_product_day_unknown"],
        "mappings": selected,
    }


def _make_run(
    root: str,
    *,
    test_grid: tuple[datetime, datetime] | None = None,
    test_aliases: tuple[str, ...] | None = None,
) -> dict:
    root = _root(root)
    bundle = seed.build_seed(mode="SIMULATION_ONLY")
    if not seed.verify_seed(bundle, mode="SIMULATION_ONLY"):
        raise SimulationScanError("SCAN_SEED_UNAVAILABLE")
    value = json.loads(bundle.payload)
    aliases = (
        tuple(m["legacy_instrument_id"] for m in value["mappings"])
        if test_aliases is None
        else test_aliases
    )
    start, end = (_START, _END) if test_grid is None else test_grid
    if (
        type(aliases) is not tuple
        or not aliases
        or any(type(a) is not str or _TOKEN.fullmatch(a) is None for a in aliases)
        or type(start) is not datetime
        or type(end) is not datetime
        or start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
        or not _START <= start < end <= _END
        or (start - _START) % _STEP
        or (end - start) % _STEP
        or (test_grid is not None and end - start > timedelta(days=2))
    ):
        raise SimulationScanError("SCAN_INPUT_INVALID")
    manifest = _manifest(bundle, start=start, end=end, aliases=aliases, test=test_grid is not None)
    directory = None
    try:
        os.mkdir(root, 0o700)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _write_new(directory, ".lock", b"")
        _write_new(directory, "seed.json", bundle.payload)
        _write_new(directory, "manifest.json", _json(manifest))
    except Exception:
        raise SimulationScanError("SCAN_INIT_UNKNOWN") from None
    finally:
        if directory is not None:
            os.close(directory)
    return {
        "status": "INITIALIZED",
        "scope": manifest["scope"],
        "seed_sha256": bundle.sha256,
        "version_sha256": value["version_sha256"],
        "expected_points": manifest["expected_points"],
    }


def init_run(root: str) -> dict:
    """Initialize only; annual scan is started solely by a later explicit run."""
    return _make_run(root)


def _load_run(dirfd: int, *, allow_test: bool = False):
    try:
        manifest = _read_object(dirfd, "manifest.json")
        payload = _read_file(dirfd, "seed.json")
        bundle = seed.LegacySeedBundle(payload, manifest["seed_sha256"])
        if not seed.verify_seed(bundle, mode="SIMULATION_ONLY"):
            raise ValueError("seed mismatch")
        test = manifest.get("scope") == "TEST_ONLY_NOT_ANNUAL"
        if test and not allow_test:
            raise ValueError("test run")
        aliases = tuple(item["legacy_instrument_id"] for item in manifest["mappings"])
        expected = _manifest(
            bundle,
            start=datetime.fromisoformat(manifest["start_utc"]),
            end=datetime.fromisoformat(manifest["end_utc"]),
            aliases=aliases,
            test=test,
        )
        if expected != manifest:
            raise ValueError("manifest mismatch")
        return manifest, bundle
    except Exception:
        raise SimulationScanError("SCAN_IDENTITY_UNAVAILABLE") from None


class _Gate:
    def verify(self, event, context):
        raise RuntimeError("simulation gate not used")


class _ReadOnlyStore:
    def __init__(self, store):
        self.store = store

    def read_current(self):
        return self.store.read_current_with_consistent_receipts(budget=_BUDGET).snapshot


@contextmanager
def _temporary_store(bundle):
    value = json.loads(bundle.payload)
    try:
        root = tempfile.mkdtemp(prefix="legacy-scan-mock-")
    except Exception:
        raise SimulationScanError("SCAN_STORE_UNAVAILABLE") from None
    completed = False
    try:
        try:
            store = TradingCalendarStore(root)
            policy = store.initialize_review_policy(config_read_succeeded=True)
            difference = {}
            claim = PolicyPublication(
                policy.directory_id,
                policy.revision,
                policy.mode,
                value["version_sha256"],
                difference,
                _sha(_json(difference)),
            )
            result = store.publish(
                value["snapshot"],
                publication_id="mock-legacy-p1",
                version_id="mock-legacy-v1",
                coverage_manifest_bytes=_json(value["manifest"]),
                coverage_manifest_sha256=value["manifest_sha256"],
                binding_bytes=_json(value["binding"]),
                binding_sha256=value["binding_sha256"],
                expected_current=CurrentPrecondition.absent(),
                policy_publication=claim,
            )
            if result.visibility != "COMMITTED":
                raise SimulationScanError("SCAN_STORE_UNAVAILABLE")
        except Exception:
            raise SimulationScanError("SCAN_STORE_UNAVAILABLE") from None
        yield root
        completed = True
    finally:
        # A failed or interrupted scratch publication remains available for
        # local inspection; never erase an uncertain creation automatically.
        if completed:
            with suppress(OSError):
                shutil.rmtree(root)


def _view(store_root: str, manifest: dict, bundle: seed.LegacySeedBundle):
    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    bound = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=_ReadOnlyStore(TradingCalendarStore(store_root)),
        snapshot_factory=CalendarSnapshot,
        gate=_Gate(),
        route="mock-scan",
        source_generation="mock-scan-v1",
        trading_day=date(2026, 1, 6),
        last_sequence=1,
    )
    view = bound.capture_read_view(mode="SIMULATION_ONLY")
    status = view.status()
    if (
        _sha(bundle.payload) != manifest["seed_sha256"]
        or bundle.sha256 != manifest["seed_sha256"]
        or status.version_sha256 != manifest["version_sha256"]
        or status.content_sha256 != manifest["content_sha256"]
        or status.calendar_ref != "mock-legacy-v1"
        or status.load_error is not None
    ):
        raise SimulationScanError("SCAN_SNAPSHOT_MISMATCH")
    return view


def _prepared(view, mapping, manifest):
    from bt_api_ctp.simulation_calendar_ports import (
        SimulationMapping,
        SimulationOwnerClosure,
        create_prepared_simulation_calendar_ports,
    )

    h = manifest["horizon"]
    c = SimulationOwnerClosure(
        manifest["version_sha256"],
        datetime.fromisoformat(h["start"]),
        datetime.fromisoformat(h["end"]),
        date.fromisoformat(manifest["owner_start"]),
        date.fromisoformat(manifest["owner_end"]),
        manifest["max_advance_days"],
        manifest["max_spill_days"],
    )
    m = SimulationMapping(
        mapping["legacy_instrument_id"],
        mapping["venue"],
        mapping["kind"],
        mapping["product"],
        mapping["mock_contract"],
    )
    return create_prepared_simulation_calendar_ports(
        mode="SIMULATION_ONLY",
        view=view,
        mappings=(m,),
        closure=c,
        required_instruments=(m.instrument_id,),
        required_years=((m.venue, 2026),),
    )


def _window_tuple(window):
    try:
        kind = window.kind.value
        if (
            kind not in {"auction", "continuous"}
            or type(window.trading_day) is not date
            or not date(2026, 1, 1) <= window.trading_day <= date(2026, 12, 31)
            or type(window.start_utc) is not datetime
            or window.start_utc.utcoffset() != timedelta(0)
            or type(window.end_utc) is not datetime
            or window.end_utc.utcoffset() != timedelta(0)
            or window.start_utc >= window.end_utc
            or type(window.session_id) is not str
        ):
            return None
        return (kind, window.trading_day, window.start_utc, window.end_utc, window.session_id)
    except Exception:
        return None


def _state(schedule, alias, instant, *, legacy):
    try:
        row = schedule.session_state(alias, instant)
        phase = row.phase.value
        if (
            row.instrument_id != alias
            or row.at_utc != instant
            or phase not in {"closed", "auction", "continuous"}
            or row.calendar_ref != ("ctp.trading-sessions.2026.v1" if legacy else "mock-legacy-v1")
        ):
            return None
        if phase in {"auction", "continuous"}:
            current = _window_tuple(row.current_window)
            if (
                current is None
                or current[0] != phase
                or not current[2] <= instant < current[3]
                or current[1] != row.trading_day
            ):
                return None
        else:
            following = _window_tuple(row.next_continuous)
            if (
                row.current_window is not None
                or type(row.trading_day) is not date
                or following is None
                or following[0] != "continuous"
                or following[1] != row.trading_day
                or following[2] <= instant
            ):
                return None
        return row
    except Exception:
        return None


def _windows(schedule, alias, instant):
    try:
        value = schedule.windows(alias, instant, instant + _STEP)
        if type(value) is not tuple:
            return None
        converted = tuple(_window_tuple(w) for w in value)
        if any(
            item is None or not item[2] < instant + _STEP or item[3] <= instant
            for item in converted
        ):
            return None
        return converted
    except Exception:
        return None


def _compare(legacy, modern, alias: str, instant: datetime) -> str:
    old = _state(legacy, alias, instant, legacy=True)
    new = _state(modern, alias, instant, legacy=False)
    old_windows = _windows(legacy, alias, instant)
    new_windows = _windows(modern, alias, instant)
    if old is None:
        # The tuple port has no identity field of its own.
        old_windows = None
    if new is None:
        # The tuple port has no identity field of its own.
        new_windows = None
    if not _SAFE_START <= instant < _SAFE_END:
        new, new_windows = None, None
    phase = "U" if old is None or new is None else "E" if old.phase == new.phase else "M"
    old_owner = getattr(old, "trading_day", None)
    new_owner = getattr(new, "trading_day", None)
    owner = (
        "U"
        if old is None
        or new is None
        or type(old_owner) is not date
        or type(new_owner) is not date
        or not date(2026, 1, 1) <= old_owner <= date(2026, 12, 31)
        or not date(2026, 1, 1) <= new_owner <= date(2026, 12, 31)
        else "E"
        if old_owner == new_owner
        else "M"
    )
    old_next = _window_tuple(getattr(old, "next_continuous", None))
    new_next = _window_tuple(getattr(new, "next_continuous", None))
    following = (
        "U"
        if old is None
        or new is None
        or old_next is None
        or new_next is None
        or old_next[0] != "continuous"
        or new_next[0] != "continuous"
        or old_next[2] <= instant
        or new_next[2] <= instant
        or old_next[2] < _SAFE_START
        or old_next[3] > _SAFE_END
        or new_next[2] < _SAFE_START
        or new_next[3] > _SAFE_END
        else "E"
        if old_next == new_next
        else "M"
    )
    windows = (
        "U"
        if old_windows is None
        or not old_windows
        or new_windows is None
        or any(item[2] < _SAFE_START or item[3] > _SAFE_END for item in old_windows)
        or any(item[2] < _SAFE_START or item[3] > _SAFE_END for item in new_windows)
        else "E"
        if old_windows == new_windows
        else "M"
    )
    self_check = "U"
    if old is not None and old_windows is not None:
        active = [item for item in old_windows if item[2] <= instant < item[3]]
        if old.phase.value in {"auction", "continuous"}:
            current = _window_tuple(old.current_window)
            self_check = "N" if len(active) == 1 and active[0] == current else "C"
        elif active:
            self_check = "C"
    return phase + owner + following + windows + self_check


def _counts(runs: list, total: int) -> dict:
    counts = {name: {"VALUE_EQUAL": 0, "MISMATCH": 0, "UNKNOWN": 0} for name in _DIMENSIONS}
    conflict = {"CONFLICT": 0, "NO_POSITIVE_CONFLICT": 0, "UNKNOWN": 0}
    cursor = 0
    previous = None
    for run in runs:
        if (
            type(run) is not list
            or len(run) != 3
            or type(run[0]) is not int
            or type(run[1]) is not int
            or run[0] != cursor
            or run[1] <= 0
            or type(run[2]) is not str
            or len(run[2]) != 5
            or any(ch not in "EMU" for ch in run[2][:4])
            or run[2][4] not in "CNU"
            or run[2] == previous
        ):
            raise ValueError("invalid runs")
        cursor += run[1]
        if cursor > total:
            raise ValueError("invalid runs")
        for i, name in enumerate(_DIMENSIONS):
            counts[name][{"E": "VALUE_EQUAL", "M": "MISMATCH", "U": "UNKNOWN"}[run[2][i]]] += run[1]
        conflict[{"C": "CONFLICT", "N": "NO_POSITIVE_CONFLICT", "U": "UNKNOWN"}[run[2][4]]] += run[
            1
        ]
        previous = run[2]
    if cursor != total:
        raise ValueError("incomplete runs")
    return {"dimensions": counts, "legacy_state_windows": conflict}


def _samples(runs: list) -> dict:
    samples = {"MISMATCH": [], "UNKNOWN": [], "LEGACY_CONFLICT": []}
    for start, length, code in runs:
        categories = (
            ("MISMATCH", "M" in code[:4]),
            ("UNKNOWN", "U" in code[:4]),
            ("LEGACY_CONFLICT", code[4] == "C"),
        )
        for label, selected in categories:
            if selected:
                for index in range(start, min(start + length, start + 8 - len(samples[label]))):
                    samples[label].append({"point_index": index, "code": code})
    return samples


def _scan_interval(legacy, modern, alias: str, start: datetime, total: int) -> dict:
    runs = []
    for index in range(total):
        code = _compare(legacy, modern, alias, start + index * _STEP)
        if runs and runs[-1][2] == code:
            runs[-1][1] += 1
        else:
            runs.append([index, 1, code])
    return {"total": total, "runs": runs, "counts": _counts(runs, total), "samples": _samples(runs)}


def compare_interval(alias: str, start: datetime, end: datetime) -> dict:
    """Directly compare at most two days; result is never an annual scan."""
    from bt_api_ctp.trading_session_schedule import CtpTradingSessionSchedule

    if (
        type(alias) is not str
        or _TOKEN.fullmatch(alias) is None
        or type(start) is not datetime
        or type(end) is not datetime
        or start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
        or not _START <= start < end <= _END
        or (start - _START) % _STEP
        or (end - start) % _STEP
        or end - start > timedelta(days=2)
    ):
        raise SimulationScanError("SCAN_INPUT_INVALID")
    bundle = seed.build_seed(mode="SIMULATION_ONLY")
    if not seed.verify_seed(bundle, mode="SIMULATION_ONLY"):
        raise SimulationScanError("SCAN_SEED_UNAVAILABLE")
    manifest = _manifest(bundle, start=start, end=end, aliases=(alias,), test=True)
    with _temporary_store(bundle) as root:
        _assert_runtime(manifest)
        view = _view(root, manifest, bundle)
        ports = _prepared(view, manifest["mappings"][0], manifest)
        result = _scan_interval(
            CtpTradingSessionSchedule(), ports.schedule, alias, start, manifest["points_per_shard"]
        )
        _assert_runtime(manifest)
    return {
        "status": "TEST_ONLY_NOT_ANNUAL",
        "alias": alias,
        "seed_sha256": bundle.sha256,
        "version_sha256": manifest["version_sha256"],
        "source": "MOCK_LEGACY_DERIVED",
        "source_authentication": "NOT_PROVEN",
        **result,
    }


def _envelope(body: dict) -> bytes:
    return _json({"body": body, "sha256": _sha(_json(body))})


def _verify_envelope(obj: dict) -> dict:
    if (
        set(obj) != {"body", "sha256"}
        or type(obj["body"]) is not dict
        or obj["sha256"] != _sha(_json(obj["body"]))
    ):
        raise ValueError("invalid digest")
    return obj["body"]


def _claim_body(manifest_sha: str, alias: str, total: int) -> dict:
    return {
        "schema": "bt_api.legacy-scan-claim/1",
        "manifest_sha256": manifest_sha,
        "alias": alias,
        "total": total,
    }


def _verify_shard(obj: dict, manifest_sha: str, alias: str, total: int) -> dict:
    body = _verify_envelope(obj)
    if set(body) != {
        "schema",
        "manifest_sha256",
        "alias",
        "total",
        "runs",
        "counts",
        "samples",
    } or (
        body["schema"] != "bt_api.legacy-scan-shard/1"
        or body["manifest_sha256"] != manifest_sha
        or body["alias"] != alias
        or type(body["total"]) is not int
        or body["total"] != total
        or type(body["runs"]) is not list
        or len(body["runs"]) > total
        or body["counts"] != _counts(body["runs"], total)
        or body["samples"] != _samples(body["runs"])
    ):
        raise ValueError("invalid shard")
    return body


def _worker(store_root: str, manifest: dict, bundle: seed.LegacySeedBundle, mapping: dict) -> bytes:
    from bt_api_ctp.trading_session_schedule import CtpTradingSessionSchedule

    _assert_runtime(manifest)
    view = _view(store_root, manifest, bundle)
    ports = _prepared(view, mapping, manifest)
    alias = mapping["legacy_instrument_id"]
    result = _scan_interval(
        CtpTradingSessionSchedule(),
        ports.schedule,
        alias,
        datetime.fromisoformat(manifest["start_utc"]),
        manifest["points_per_shard"],
    )
    _assert_runtime(manifest)
    body = {
        "schema": "bt_api.legacy-scan-shard/1",
        "manifest_sha256": _sha(_json(manifest)),
        "alias": alias,
        **result,
    }
    return _envelope(body)


def _dates(view, manifest, bundle):
    from bt_api_ctp.futures_expected_calendar_provider import expected_trading_days

    values = []
    mappings = json.loads(bundle.payload)["mappings"]
    for venue in ("SHFE", "INE", "CZCE", "DCE"):
        try:
            days = expected_trading_days(venue, 2026)
            if (
                type(days) is not tuple
                or not days
                or any(type(day) is not date for day in days)
                or tuple(sorted(set(days))) != days
            ):
                raise ValueError("invalid legacy days")
            expected = set(days)
        except Exception:
            expected = None
        try:
            mapping = next(item for item in mappings if item["venue"] == venue)
            days = _prepared(view, mapping, manifest).expected_days.expected_trading_days(
                venue, 2026
            )
            if (
                type(days) is not tuple
                or any(type(day) is not date for day in days)
                or tuple(sorted(set(days))) != days
            ):
                raise ValueError("invalid simulated days")
            simulated = set(days)
        except Exception:
            simulated = None
        for offset in range(365):
            day = date(2026, 1, 1) + timedelta(days=offset)
            try:
                row = view.query_venue_day(mode="SIMULATION_ONLY", venue=venue, trading_day=day)
                valid = (
                    expected is not None
                    and simulated is not None
                    and row.status == "COMPLETE"
                    and row.binding.version_sha256 == manifest["version_sha256"]
                    and row.binding.content_sha256 == manifest["content_sha256"]
                    and row.binding.calendar_ref == "mock-legacy-v1"
                    and row.venue == venue
                    and row.trading_day == day
                    and row.source_ids == ("MOCK.simulation_only",)
                    and row.source_authentication == "NOT_PROVEN"
                    and row.day_status in {"open", "closed"}
                    and (row.day_status == "open") == (day in simulated)
                )
            except Exception:
                valid = False
            if not valid:
                code = "UNKNOWN"
            else:
                code = "VALUE_EQUAL" if (day in simulated) == (day in expected) else "MISMATCH"
            values.append([venue, day.isoformat(), code])
    return {
        "schema": "bt_api.legacy-scan-dates/1",
        "manifest_sha256": _sha(_json(manifest)),
        "values": values,
    }


def _check_dates(body: dict, manifest: dict):
    if (
        set(body) != {"schema", "manifest_sha256", "values"}
        or body["schema"] != "bt_api.legacy-scan-dates/1"
        or body["manifest_sha256"] != _sha(_json(manifest))
        or type(body["values"]) is not list
        or len(body["values"]) != 1460
    ):
        raise ValueError("invalid dates")
    expected = [
        (venue, (date(2026, 1, 1) + timedelta(days=i)).isoformat())
        for venue in ("SHFE", "INE", "CZCE", "DCE")
        for i in range(365)
    ]
    if any(
        type(row) is not list
        or len(row) != 3
        or (row[0], row[1]) != scope
        or row[2] not in {"VALUE_EQUAL", "MISMATCH", "UNKNOWN"}
        for row, scope in zip(body["values"], expected, strict=True)
    ):
        raise ValueError("invalid dates")


def _existing(dirfd, name):
    try:
        os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _shard_status(dirfd, manifest, mapping):
    alias = mapping["legacy_instrument_id"]
    claim_name, shard_name = f"claim-{alias}.json", f"shard-{alias}.json"
    claim_exists, shard_exists = _existing(dirfd, claim_name), _existing(dirfd, shard_name)
    if not claim_exists and not shard_exists:
        return "MISSING"
    if not claim_exists or not shard_exists:
        return "UNKNOWN"
    try:
        if _verify_envelope(_read_object(dirfd, claim_name)) != _claim_body(
            _sha(_json(manifest)), alias, manifest["points_per_shard"]
        ):
            return "UNKNOWN"
        _verify_shard(
            _read_object(dirfd, shard_name),
            _sha(_json(manifest)),
            alias,
            manifest["points_per_shard"],
        )
        return "COMPLETE"
    except Exception:
        return "UNKNOWN"


def inspect_run(root: str, *, _allow_test=False) -> dict:
    try:
        with _locked(root) as directory:
            manifest, _ = _load_run(directory, allow_test=_allow_test)
            statuses = {
                m["legacy_instrument_id"]: _shard_status(directory, manifest, m)
                for m in manifest["mappings"]
            }
            date_status = "MISSING"
            if _existing(directory, "dates-claim.json") or _existing(directory, "dates.json"):
                date_status = "UNKNOWN"
                try:
                    claim = _verify_envelope(_read_object(directory, "dates-claim.json"))
                    if claim == {"manifest_sha256": _sha(_json(manifest))}:
                        _check_dates(
                            _verify_envelope(_read_object(directory, "dates.json")), manifest
                        )
                        date_status = "COMPLETE"
                except Exception:
                    date_status = "UNKNOWN"
            return {
                "status": "LOCAL_INSPECTION",
                "scope": manifest["scope"],
                "shards": statuses,
                "dates": date_status,
                "seed_sha256": manifest["seed_sha256"],
                "version_sha256": manifest["version_sha256"],
                "expected_points": manifest["expected_points"],
                "durability": "NOT_PROVEN",
                "source_authentication": "NOT_PROVEN",
            }
    except SimulationScanError:
        raise
    except Exception:
        raise SimulationScanError("SCAN_INSPECTION_UNAVAILABLE") from None


def run_scan(root: str, *, workers: int = 2, _allow_test: bool = False, progress=None) -> dict:
    if type(workers) is not int or not 1 <= workers <= 4:
        raise SimulationScanError("SCAN_WORKER_LIMIT")
    with _locked(root) as directory:
        manifest, bundle = _load_run(directory, allow_test=_allow_test)
        with _temporary_store(bundle) as store_root:
            view = _view(store_root, manifest, bundle)
            if not _existing(directory, "dates-claim.json") and not _existing(
                directory, "dates.json"
            ):
                _write_new(
                    directory,
                    "dates-claim.json",
                    _envelope({"manifest_sha256": _sha(_json(manifest))}),
                )
                dates = _dates(view, manifest, bundle)
                _assert_runtime(manifest)
                _write_new(directory, "dates.json", _envelope(dates))
            try:
                if _verify_envelope(_read_object(directory, "dates-claim.json")) != {
                    "manifest_sha256": _sha(_json(manifest))
                }:
                    raise ValueError("invalid claim")
                _check_dates(_verify_envelope(_read_object(directory, "dates.json")), manifest)
            except Exception:
                raise SimulationScanError("SCAN_DATES_UNKNOWN") from None
            missing = [
                m
                for m in manifest["mappings"]
                if _shard_status(directory, manifest, m) == "MISSING"
            ]
            uncertain = set()
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=multiprocessing.get_context("spawn")
            ) as pool:
                pending = {}
                while missing or pending:
                    while missing and len(pending) < workers:
                        mapping = missing.pop(0)
                        alias = mapping["legacy_instrument_id"]
                        try:
                            _assert_runtime(manifest)
                            _write_new(
                                directory,
                                f"claim-{alias}.json",
                                _envelope(
                                    _claim_body(
                                        _sha(_json(manifest)), alias, manifest["points_per_shard"]
                                    )
                                ),
                            )
                            future = pool.submit(_worker, store_root, manifest, bundle, mapping)
                            pending[future] = mapping
                        except Exception:
                            # Do not dispatch after an uncertain create/submit.
                            uncertain.add(alias)
                            missing.clear()
                            if progress is not None:
                                progress({"product": mapping["product"], "status": "UNKNOWN"})
                            break
                    if not pending:
                        break
                    finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        mapping = pending.pop(future)
                        alias = mapping["legacy_instrument_id"]
                        try:
                            payload = future.result()
                            _verify_shard(
                                json.loads(payload),
                                _sha(_json(manifest)),
                                alias,
                                manifest["points_per_shard"],
                            )
                        except Exception:
                            if progress is not None:
                                progress({"product": mapping["product"], "status": "UNKNOWN"})
                            continue
                        try:
                            _assert_runtime(manifest)
                            _write_new(directory, f"shard-{alias}.json", payload)
                            if progress is not None:
                                progress({"product": mapping["product"], "status": "COMPLETE"})
                        except Exception:
                            uncertain.add(alias)
                            missing.clear()
                            if progress is not None:
                                progress({"product": mapping["product"], "status": "UNKNOWN"})
            statuses = {
                m["legacy_instrument_id"]: (
                    "UNKNOWN"
                    if m["legacy_instrument_id"] in uncertain
                    else _shard_status(directory, manifest, m)
                )
                for m in manifest["mappings"]
            }
            return {
                "status": "SCANNED_NOT_ACCEPTANCE"
                if all(v == "COMPLETE" for v in statuses.values())
                else "INCOMPLETE",
                "scope": manifest["scope"],
                "shards": statuses,
                "durability": "NOT_PROVEN",
            }


def aggregate(root: str, *, _allow_test: bool = False) -> dict:
    with _locked(root) as directory:
        manifest, _ = _load_run(directory, allow_test=_allow_test)
        try:
            if _verify_envelope(_read_object(directory, "dates-claim.json")) != {
                "manifest_sha256": _sha(_json(manifest))
            }:
                raise ValueError("invalid dates claim")
            dates = _verify_envelope(_read_object(directory, "dates.json"))
            _check_dates(dates, manifest)
            rows = []
            for mapping in manifest["mappings"]:
                alias = mapping["legacy_instrument_id"]
                if _shard_status(directory, manifest, mapping) != "COMPLETE":
                    raise ValueError("incomplete shard")
                body = _verify_shard(
                    _read_object(directory, f"shard-{alias}.json"),
                    _sha(_json(manifest)),
                    alias,
                    manifest["points_per_shard"],
                )
                rows.append(
                    {
                        "product": mapping["product"],
                        "venue": mapping["venue"],
                        "alias": alias,
                        "total": body["total"],
                        "counts": body["counts"],
                        "samples": body["samples"],
                    }
                )
            if sum(row["total"] for row in rows) != manifest["expected_points"]:
                raise ValueError("incomplete count")
            totals = {
                name: {
                    label: sum(row["counts"]["dimensions"][name][label] for row in rows)
                    for label in ("VALUE_EQUAL", "MISMATCH", "UNKNOWN")
                }
                for name in _DIMENSIONS
            }
            conflict = {
                label: sum(row["counts"]["legacy_state_windows"][label] for row in rows)
                for label in ("CONFLICT", "NO_POSITIVE_CONFLICT", "UNKNOWN")
            }
            return {
                "status": "SCANNED_NOT_ACCEPTANCE",
                "scope": manifest["scope"],
                "total": manifest["expected_points"],
                "products": rows,
                "dates": dates["values"],
                "seed_sha256": manifest["seed_sha256"],
                "counts": {"dimensions": totals, "legacy_state_windows": conflict},
                "version_sha256": manifest["version_sha256"],
                "source": "MOCK_LEGACY_DERIVED",
                "source_authentication": "NOT_PROVEN",
                "independent_venue_evidence": "NOT_PROVEN",
                "durability": "NOT_PROVEN",
            }
        except Exception:
            raise SimulationScanError("SCAN_AGGREGATE_INCOMPLETE") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SimulationScanError("SCAN_INPUT_INVALID")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("action", choices=("init", "run", "inspect", "aggregate"))
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--workers", type=int, default=2)
    try:
        args = parser.parse_args(argv)
        if args.action == "init":
            result = init_run(args.run_root)
        elif args.action == "run":
            result = run_scan(
                args.run_root,
                workers=args.workers,
                progress=lambda row: print(_json(row).decode(), flush=True),
            )
        elif args.action == "inspect":
            result = inspect_run(args.run_root)
        else:
            result = aggregate(args.run_root)
        print(_json(result).decode())
        return 0 if result["status"] not in {"INCOMPLETE"} else 2
    except Exception as exc:
        reason = str(exc) if type(exc) is SimulationScanError else "SCAN_UNAVAILABLE"
        print(_json({"status": "UNKNOWN", "reason": reason, "durability": "NOT_PROVEN"}).decode())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
