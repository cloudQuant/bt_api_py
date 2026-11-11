"""Read-only, experimental check of explicit calendar config and CTP supplement.

Usage: python -m bt_api_py.trading_calendar_check --config /absolute/config.yaml
       --calendar-root /absolute/calendar --supplement /absolute/supplement.yaml

No supplement location is inferred; the explicit path is independent of root.
This command does not read CURRENT, contact
an account/network, send mail, approve or publish. Config root comparison is
lexical, not proof of directory identity or coordinated policy. CTP input is
UNVERIFIED and can be an editor's valid intermediate state. Paths, raw YAML,
source descriptions and exception messages are deliberately omitted from output.
Parent links in the supplement path are resolved with before/after observations;
the terminal file must not be a link. This detects observed replacement, not
all possible change-and-revert races, hostile writers or in-place editing.
POSIX descriptor and /dev/fd behavior, provider packaging, and cross-platform
filesystem semantics require separate acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bt_api_py.service_config import ServiceConfigError
from bt_api_py.service_config_io import _read_yaml
from bt_api_py.trading_calendar_config import CalendarConfigError, parse_trading_calendar_config


@dataclass(frozen=True)
class CalendarCheckReport:
    status: str
    code: str
    exit_code: int
    config_status: str = "NOT_CHECKED"
    root_kind: str = "UNKNOWN"
    review_mode: str | None = None
    review_mode_source: str | None = None
    supplement_status: str = "NOT_CHECKED"
    source_verification: str = "NOT_ASSESSED"
    venues: tuple[str, ...] = ()
    source_count: int = 0
    unused_source_count: int = 0
    trading_day_count: int = 0
    trading_hours_count: int = 0
    exception_count: int = 0
    directory_policy: str = "UNVERIFIED"
    input_stability: str = "UNVERIFIED"
    calendar_completeness: str = "NOT_ASSESSED"
    review: str = "NOT_PERFORMED"
    publication: str = "NOT_ATTEMPTED"
    maturity: str = "EXPERIMENTAL_READ_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _absolute_path(value: Any) -> Path:
    try:
        raw = os.fspath(value)
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("invalid path") from exc
    if (
        type(raw) is not str
        or not raw
        or "\x00" in raw
        or len(raw) > 4096
        or not os.path.isabs(raw)
        or ".." in raw.split(os.sep)
    ):
        raise ValueError("invalid path")
    result = Path(raw)
    if not result.name:
        raise ValueError("invalid path")
    return result


def _metadata(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class _SupplementPathError(Exception):
    def __init__(self, status: str):
        self.status = status


def _resolve_supplement(path: Path) -> tuple[Path, tuple, tuple[int, ...]]:
    """Trace every followed link and directory; never follow the final file."""
    remaining = list(path.parts[1:])
    resolved: list[str] = []
    trace = []
    links = 0
    steps = 0
    try:
        trace.append(("/", _metadata(os.lstat("/")), None))
        while remaining:
            steps += 1
            if steps > 512:
                raise _SupplementPathError("INVALID_PATH")
            name = remaining.pop(0)
            if name in ("", "."):
                continue
            if name == "..":
                if resolved:
                    resolved.pop()
                continue
            current = Path("/", *resolved, name)
            if len(os.fspath(current)) > 4096:
                raise _SupplementPathError("INVALID_PATH")
            info = os.lstat(current)
            is_last = not remaining
            if stat.S_ISLNK(info.st_mode):
                if is_last:
                    raise _SupplementPathError("SYMLINK")
                links += 1
                if links > 40:
                    raise _SupplementPathError("INVALID_PATH")
                target = os.readlink(current)
                trace.append((os.fspath(current), _metadata(info), target))
                if os.path.isabs(target):
                    resolved.clear()
                remaining = target.split(os.sep) + remaining
                continue
            if (is_last and not stat.S_ISREG(info.st_mode)) or (
                not is_last and not stat.S_ISDIR(info.st_mode)
            ):
                raise _SupplementPathError("NOT_REGULAR")
            trace.append((os.fspath(current), _metadata(info), None))
            resolved.append(name)
        if not resolved or not trace:
            raise _SupplementPathError("INVALID_PATH")
        return Path("/", *resolved), tuple(trace), trace[-1][1]
    except FileNotFoundError:
        raise _SupplementPathError("MISSING") from None
    except OSError:
        raise _SupplementPathError("READ_ERROR") from None


def _read_checked_supplement(path: Path, reader: Any) -> tuple[str, Any]:
    """Keep a target descriptor while the provider reads the no-link pathname."""
    if os.name != "posix" or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK")):
        return "UNSUPPORTED", None
    try:
        canonical, before_trace, before_file = _resolve_supplement(path)
    except _SupplementPathError as exc:
        return exc.status, None
    try:
        descriptor = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return "CHANGED", None
    try:
        if _metadata(os.fstat(descriptor)) != before_file:
            return "CHANGED", None
        result = reader(canonical)
        try:
            after_path, after_trace, after_file = _resolve_supplement(path)
            after_fd = _metadata(os.fstat(descriptor))
        except (OSError, _SupplementPathError):
            return "CHANGED", None
        if (canonical, before_trace, before_file) != (after_path, after_trace, after_file) or (
            before_file != after_fd
        ):
            return "CHANGED", None
        return result.status, result
    finally:
        os.close(descriptor)


def _read_config(path: Path) -> dict:
    """Use the existing bounded YAML loader via a held, no-follow descriptor."""
    if os.name != "posix" or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK")):
        raise ServiceConfigError("CONFIG_PLATFORM_UNSUPPORTED", "$")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ServiceConfigError("CONFIG_READ_FAILED", "$") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ServiceConfigError("CONFIG_NOT_REGULAR_FILE", "$")
        # /dev/fd duplicates the already-open descriptor; no second config-path open.
        config = _read_yaml(f"/dev/fd/{descriptor}")
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(path, follow_symlinks=False)
        except OSError:
            raise ServiceConfigError("CONFIG_CHANGED", "$") from None
        if _metadata(before) != _metadata(after) or _metadata(before) != _metadata(path_after):
            raise ServiceConfigError("CONFIG_CHANGED", "$")
        return config
    finally:
        os.close(descriptor)


def check_calendar_inputs(
    *,
    config_file: str | os.PathLike[str],
    calendar_root: str | os.PathLike[str],
    supplement_file: str | os.PathLike[str],
    gateway_enabled: bool = False,
    direct_write_enabled: bool = False,
) -> CalendarCheckReport:
    """Check only explicitly supplied paths, returning finite non-secret diagnostics.

    Exit 0 means a parsed but UNVERIFIED supplement candidate, not a complete
    calendar or a release decision. 2 is configuration/path failure; 3 is a
    missing supplement; 4 is an invalid/unstable supplement; 5 means a required
    platform or dependency is unavailable. No result authorizes a write.
    """
    try:
        config_path = _absolute_path(config_file)
        root_path = _absolute_path(calendar_root)
        supplement_path = _absolute_path(supplement_file)
    except ValueError:
        return CalendarCheckReport("ERROR", "INVALID_ABSOLUTE_PATH", 2)
    try:
        loaded = _read_config(config_path)
    except ImportError:
        return CalendarCheckReport("ERROR", "CONFIG_DEPENDENCY_UNAVAILABLE", 5)
    except ServiceConfigError as exc:
        code = (
            exc.code
            if exc.code
            in {
                "CONFIG_READ_FAILED",
                "CONFIG_NOT_REGULAR_FILE",
                "CONFIG_TOO_LARGE",
                "CONFIG_ENCODING_INVALID",
                "CONFIG_YAML_INVALID",
                "CONFIG_DUPLICATE_KEY",
                "CONFIG_ALIAS_FORBIDDEN",
                "CONFIG_ROOT_INVALID",
                "CONFIG_KEY_INVALID",
                "CONFIG_TOO_COMPLEX",
                "CONFIG_TOO_DEEP",
                "CONFIG_CHANGED",
                "CONFIG_PLATFORM_UNSUPPORTED",
            }
            else "CONFIG_INVALID"
        )
        return CalendarCheckReport("ERROR", code, 5 if code == "CONFIG_PLATFORM_UNSUPPORTED" else 2)
    except (OSError, ValueError, TypeError):
        return CalendarCheckReport("ERROR", "CONFIG_READ_FAILED", 2)
    try:
        parsed = parse_trading_calendar_config(
            loaded,
            config_file=config_path,
            gateway_enabled=gateway_enabled,
            direct_write_enabled=direct_write_enabled,
        )
    except CalendarConfigError as exc:
        # Config parser exposes a finite code, never the config content.
        code = (
            exc.code
            if exc.code
            in {
                "calendar_config_invalid",
                "calendar_config_invalid_mode",
                "calendar_config_invalid_file_path",
                "calendar_config_unknown_field",
                "calendar_review_mode_invalid",
                "calendar_root_invalid",
                "calendar_root_invalid_home",
            }
            else "calendar_config_invalid"
        )
        return CalendarCheckReport("ERROR", code, 2, config_status="INVALID")
    common = {
        "config_status": parsed.status,
        "root_kind": parsed.root_kind,
        "review_mode": parsed.review_mode,
        "review_mode_source": parsed.review_mode_source,
    }
    if parsed.status != "CONFIGURED":
        return CalendarCheckReport("ERROR", "CALENDAR_ROOT_UNCONFIGURED", 2, **common)
    if parsed.root_kind != "directory" or parsed.root_path is None:
        return CalendarCheckReport("ERROR", "EXTERNAL_ROOT_REQUIRED", 2, **common)
    if Path(os.path.normpath(root_path)) != parsed.root_path:
        return CalendarCheckReport("ERROR", "CALENDAR_ROOT_MISMATCH", 2, **common)
    try:
        root_stat = os.lstat(root_path)
    except OSError:
        return CalendarCheckReport("ERROR", "CALENDAR_ROOT_UNAVAILABLE", 2, **common)
    if not stat.S_ISDIR(root_stat.st_mode):
        return CalendarCheckReport("ERROR", "CALENDAR_ROOT_NOT_DIRECTORY", 2, **common)
    try:
        from bt_api_ctp.calendar_supplement_reader import read_supplement
    except ImportError:
        return CalendarCheckReport("ERROR", "CTP_READER_UNAVAILABLE", 5, **common)
    try:
        status, result = _read_checked_supplement(supplement_path, read_supplement)
    except Exception:
        return CalendarCheckReport("ERROR", "SUPPLEMENT_READER_ERROR", 5, **common)
    if type(status) is not str:
        return CalendarCheckReport("ERROR", "SUPPLEMENT_READER_ERROR", 5, **common)
    supported_errors = {
        "INVALID_PATH",
        "SYMLINK",
        "NOT_REGULAR",
        "CHANGED",
        "TOO_LARGE",
        "INVALID_UTF8",
        "INVALID_CONTENT",
        "READ_ERROR",
        "PARSER_ERROR",
    }
    if status == "MISSING":
        return CalendarCheckReport(
            "ERROR", "SUPPLEMENT_MISSING", 3, supplement_status="MISSING", **common
        )
    if status in ("UNSUPPORTED", "DEPENDENCY_UNAVAILABLE"):
        return CalendarCheckReport(
            "ERROR", "SUPPLEMENT_" + status, 5, supplement_status=status, **common
        )
    if status in supported_errors:
        return CalendarCheckReport(
            "ERROR", "SUPPLEMENT_" + status, 4, supplement_status=status, **common
        )
    try:
        if (
            result.status != "CANDIDATE"
            or result.verification != "UNVERIFIED"
            or result.candidate is None
            or result.candidate.verification != "UNVERIFIED"
        ):
            raise ValueError("unexpected reader response")
        candidate = result.candidate
        content = candidate.content
        venues = tuple(
            sorted(
                {
                    row["venue"]
                    for layer in ("trading_days", "trading_hours", "exceptions")
                    for row in content[layer]
                }
            )
        )
        if not set(venues) <= {"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"}:
            raise ValueError("invalid reader venue")
        return CalendarCheckReport(
            "CHECKED",
            "SUPPLEMENT_UNVERIFIED",
            0,
            supplement_status="CANDIDATE",
            source_verification="UNVERIFIED",
            venues=venues,
            source_count=len(content["sources"]),
            unused_source_count=len(candidate.unused_source_ids),
            trading_day_count=len(content["trading_days"]),
            trading_hours_count=len(content["trading_hours"]),
            exception_count=len(content["exceptions"]),
            **common,
        )
    except Exception:
        return CalendarCheckReport("ERROR", "SUPPLEMENT_READER_ERROR", 5, **common)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError()


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--config", required=True, help="absolute path to YAML configuration")
    parser.add_argument("--calendar-root", required=True, help="explicit absolute calendar root")
    parser.add_argument("--supplement", required=True, help="explicit absolute CTP supplement path")
    parser.add_argument("--gateway-enabled", action="store_true")
    parser.add_argument("--direct-write-enabled", action="store_true")
    try:
        args = parser.parse_args(argv)
    except _UsageError:
        report = CalendarCheckReport("ERROR", "INVALID_ARGUMENTS", 2)
    else:
        report = check_calendar_inputs(
            config_file=args.config,
            calendar_root=args.calendar_root,
            supplement_file=args.supplement,
            gateway_enabled=args.gateway_enabled,
            direct_write_enabled=args.direct_write_enabled,
        )
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
