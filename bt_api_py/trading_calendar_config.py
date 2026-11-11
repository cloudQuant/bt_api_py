"""Experimental, detached calendar configuration parsing; no runtime integration.

This candidate uses ``calendars.root`` and the provisional, unfrozen
``calendars.review_mode`` key. The input must be a successfully parsed mapping
or an explicit read/parse failure; this module does not read the config file.
Paths are normalized lexically, including ``..``, without resolving symlinks.
Lexical normalization can differ from filesystem traversal through a symlink;
later secure access must handle symlinks and directory identity explicitly. No symlink,
filesystem identity, existence, network, locking, or multi-process policy check
is performed. In particular, an "auto" result is only the declared/default
mode, never authorization to publish. Directory policy truth and coordinated
revalidation are still required. Windows UNC and long paths remain unverified.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CalendarConfigError(ValueError):
    """A configuration or its input cannot be interpreted safely."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CalendarConfigReadFailure:
    """Explicit caller report; ``kind`` is ``unreadable`` or ``invalid_format``."""

    kind: str


@dataclass(frozen=True)
class CalendarConfigCandidate:
    status: str
    reason: str
    root_kind: str
    root_path: Path | None
    review_mode: str
    review_mode_source: str
    # None means unknown, not that the external directory is updated daily.
    daily_updates: bool | None
    directory_policy_verified: bool = False
    maturity: str = "EXPERIMENTAL"


def parse_trading_calendar_config(
    config: Mapping[str, Any] | CalendarConfigReadFailure,
    *,
    config_file: str | os.PathLike[str],
    gateway_enabled: bool,
    direct_write_enabled: bool,
) -> CalendarConfigCandidate:
    """Parse caller-supplied settings without reading files or enabling a calendar.

    Missing root reports ``calendar_root_unconfigured`` when Gateway or direct
    writing is enabled; other unconfigured scenarios remain UNKNOWN. ``None``
    and malformed configs never represent successful empty configuration.
    """
    if isinstance(config, CalendarConfigReadFailure):
        if config.kind not in {"unreadable", "invalid_format"}:
            raise CalendarConfigError("calendar_config_invalid_failure")
        raise CalendarConfigError("calendar_config_" + config.kind)
    if not isinstance(config, Mapping):
        raise CalendarConfigError("calendar_config_invalid")
    if type(gateway_enabled) is not bool or type(direct_write_enabled) is not bool:
        raise CalendarConfigError("calendar_config_invalid_mode")
    try:
        file_path = Path(config_file)
    except (TypeError, ValueError) as exc:
        raise CalendarConfigError("calendar_config_invalid_file_path") from exc
    if not file_path.is_absolute() or not file_path.name or "\x00" in str(file_path):
        raise CalendarConfigError("calendar_config_invalid_file_path")
    file_path = Path(os.path.normpath(file_path))
    if not file_path.name:
        raise CalendarConfigError("calendar_config_invalid_file_path")

    if "calendars" in config:
        calendar = config["calendars"]
        if not isinstance(calendar, Mapping):
            raise CalendarConfigError("calendar_config_invalid")
        if set(calendar) - {"root", "review_mode"}:
            raise CalendarConfigError("calendar_config_unknown_field")
    else:
        calendar = {}
    if "review_mode" in calendar:
        mode = calendar["review_mode"]
        if type(mode) is not str or mode not in {"auto", "manual"}:
            raise CalendarConfigError("calendar_review_mode_invalid")
        mode_source = "explicit"
    else:
        mode, mode_source = "auto", "default"

    def candidate(
        status: str, reason: str, kind: str, path: Path | None, updates: bool | None
    ) -> CalendarConfigCandidate:
        return CalendarConfigCandidate(status, reason, kind, path, mode, mode_source, updates)

    if "root" not in calendar:
        if gateway_enabled or direct_write_enabled:
            return candidate("ERROR", "calendar_root_unconfigured", "unconfigured", None, None)
        return candidate("UNKNOWN", "calendar_root_unconfigured", "unconfigured", None, None)
    root = calendar["root"]
    if type(root) is not str or not root.strip() or "\x00" in root:
        raise CalendarConfigError("calendar_root_invalid")
    if root == "package_seed":
        return candidate("CONFIGURED", "package_seed", "package_seed", None, False)

    expanded = os.path.expanduser(root)
    if root.startswith("~") and expanded.startswith("~"):
        raise CalendarConfigError("calendar_root_invalid_home")
    root_path = Path(expanded)
    # A drive-relative or rooted-but-drive-less path is ambiguous on Windows.
    if not root_path.is_absolute() and (root_path.drive or root_path.root):
        raise CalendarConfigError("calendar_root_invalid")
    if not root_path.is_absolute():
        root_path = file_path.parent / root_path
    root_path = Path(os.path.normpath(root_path))
    return candidate("CONFIGURED", "external_directory", "directory", root_path, None)
