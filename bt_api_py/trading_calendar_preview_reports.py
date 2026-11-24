"""Explicit, experimental retention of sanitized preview reports.

Only an already-produced PreviewReport is accepted; no configuration, source
content, candidate, account, recipient, or path is written into the report.
Names are immutable by this API and created with an exclusive temporary file
and an atomic no-replace hard link; cooperating writers must not mutate files.
The caller must explicitly assert a trusted local POSIX filesystem. That
assertion is not verified, and Windows, network filesystems, hostile writers,
filesystem corruption and power-loss durability are not supported or proven.
A crash during publication can leave an orphan temporary file or a committed
file; this API does not infer the outcome or delete files it does not own.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass, fields

from bt_api_py import _calendar_fdops as fdops
from bt_api_py.trading_calendar_preview import (
    _PAGE,
    _TRANSPORT,
    Attempt,
    PreviewReport,
    _parse_worker_result,
)

TRUSTED_LOCAL_POSIX = "TRUSTED_LOCAL_POSIX"
_SCHEMA = "bt_api.calendar-preview-report/1"
_LIMIT = 65536
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_EXIT_REASONS = {
    "INVALID_ARGUMENTS": ("ERROR", 2),
    "CLOCK_UNAVAILABLE": ("ERROR", 2),
    "INPUT_CHECK_FAILED": ("ERROR", 20),
    "PROGRESS_UNAVAILABLE": ("ERROR", 22),
    "MERGE_FAILED": ("ERROR", 22),
    "NORMALIZATION_FAILED": ("ERROR", 22),
    "SUPERVISOR_UNSUPPORTED": ("ERROR", 22),
    "WORKER_START_FAILED": ("ERROR", 22),
    "WORKER_PROTOCOL_ERROR": ("ERROR", 22),
    "WORKER_COMMUNICATION_ERROR": ("ERROR", 22),
    "WORKER_CLEANUP_UNCERTAIN": ("ERROR", 23),
    "SHFE_NOT_REQUESTED": ("INCOMPLETE", 10),
    "OTHER_VENUES_NOT_IMPLEMENTED": ("INCOMPLETE", 10),
    "SHFE_NOT_VERIFIED": ("INCOMPLETE", 10),
    "RETRY_LIMIT_REACHED": ("INCOMPLETE", 10),
    "RETRY_BUDGET_EXHAUSTED": ("INCOMPLETE", 21),
    "BUDGET_EXHAUSTED": ("INCOMPLETE", 21),
    "CHILD_CLEANUP_UNCERTAIN": ("INCOMPLETE", 23),
    "MERGE_CONFLICT": ("INCOMPLETE", 10),
}
_SUPERVISOR_REASONS = frozenset(
    {
        "SUPERVISOR_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_PROTOCOL_ERROR",
        "WORKER_COMMUNICATION_ERROR",
        "WORKER_CLEANUP_UNCERTAIN",
    }
)
_COUNTS = frozenset(
    {
        "holiday_closed_count",
        "holiday_unresolved_night_count",
        "merge_source_count",
        "merge_day_count",
        "merge_hour_count",
        "merge_exception_count",
    }
)


@dataclass(frozen=True)
class PreviewRetentionResult:
    status: str
    visibility: str
    durability: str = "UNCONFIRMED"
    run_id: str = ""
    reason: str = ""
    verification: str = "UNVERIFIED"
    publication: str = "NOT_AUTHORIZED"


def _validated_report(report: object) -> bytes | None:
    if type(report) is not PreviewReport:
        return None
    try:
        if type(report.attempts) is not tuple or type(report.holiday_attempts) is not tuple:
            return None
        if any(type(item) is not Attempt for item in (*report.attempts, *report.holiday_attempts)):
            return None
        # Capture once; all further checks and the eventual write use this exact
        # detached serialization, even if the caller later mutates the object.
        raw = json.dumps(
            report.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        if len(raw) > _LIMIT:
            return None
        data = json.loads(raw)
    except Exception:
        return None
    if type(data) is not dict or set(data) != {field.name for field in fields(PreviewReport)}:
        return None
    for field in fields(PreviewReport):
        value = data[field.name]
        if field.name in ("attempts", "holiday_attempts"):
            if type(value) is not list:
                return None
            for item in value:
                if type(item) is not dict or set(item) != {part.name for part in fields(Attempt)}:
                    return None
                if type(item["number"]) is not int or type(item["product_count"]) is not int:
                    return None
                if any(
                    type(item[name]) is not str
                    for name in (
                        "transport_status",
                        "source_status",
                        "verification",
                        "parse_status",
                    )
                ):
                    return None
        elif field.name == "exit_code":
            if type(value) is not int:
                return None
        elif field.name in _COUNTS:
            if value is not None and type(value) is not int:
                return None
        elif type(value) is not str:
            return None
    if _EXIT_REASONS.get(data["reason"]) != (data["status"], data["exit_code"]):
        return None
    if data["reason"] == "SHFE_NOT_REQUESTED" and (
        data["source_status"] != "NOT_REQUESTED"
        or data["holiday_status"] != "NOT_REQUESTED"
        or data["source_reason"] != "NOT_REQUESTED"
        or data["holiday_reason"] != "NOT_REQUESTED"
        or data["attempts"]
        or data["holiday_attempts"]
    ):
        return None
    if data["reason"] == "MERGE_CONFLICT" and data["merge_status"] != "CONFLICT":
        return None
    for status, history, is_holiday in (
        (data["source_status"], data["attempts"], False),
        (data["holiday_status"], data["holiday_attempts"], True),
    ):
        if history and status != history[-1]["source_status"]:
            return None
        if not history and status not in {"NOT_REQUESTED", "NOT_ATTEMPTED"}:
            return None
        for item in history:
            transport = item["transport_status"]
            if transport == "HTTP_OK":
                if (
                    item["source_status"] not in _PAGE
                    or item["parse_status"] != item["source_status"]
                    or (is_holiday and item["product_count"] != 0)
                ):
                    return None
            else:
                if item["source_status"] != transport or item["product_count"] != 0:
                    return None
                if transport == "PARSER_ERROR":
                    expected_parse = "PARSER_ERROR"
                elif transport in {
                    "CHILD_ERROR",
                    "CHILD_TIMEOUT",
                    "CHILD_CLEANUP_UNCERTAIN",
                    "RESULT_TOO_LARGE",
                    "CTP_UNAVAILABLE",
                    "COLLECTOR_ERROR",
                    "COLLECTOR_PROTOCOL_ERROR",
                }:
                    expected_parse = "UNKNOWN"
                elif transport in {"IN_PROGRESS", "INTERRUPTED"}:
                    expected_parse = transport
                elif transport in _TRANSPORT:
                    expected_parse = "NOT_ATTEMPTED"
                else:
                    return None
                if item["parse_status"] != expected_parse:
                    return None
    # Supervisor-only reasons are not in the worker allowlist. Validate every
    # other field with a recognized placeholder, then retain the original reason.
    try:
        checked = dict(data)
        if checked["reason"] in _SUPERVISOR_REASONS:
            checked["reason"] = "INPUT_CHECK_COMPLETE"
        checked_raw = json.dumps(checked, ensure_ascii=True, sort_keys=True).encode("ascii")
        parsed = _parse_worker_result(checked_raw, data["exit_code"])
        if (
            parsed.reason == "WORKER_PROTOCOL_ERROR"
            or json.loads(json.dumps(parsed.to_dict())) != checked
        ):
            return None
        return raw
    except Exception:
        return None


def _supported() -> bool:
    if os.name == "nt":
        from bt_api_py import _calendar_fdops as fdops
        from bt_api_py import _win32_security as security

        return bool(
            security.has_privileges_available()
            and fdops._WIN_OK
            and callable(fdops.open_root)
        )
    return (
        os.name == "posix"
        and all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY"))
        and all(fn in os.supports_dir_fd for fn in (os.open, os.stat, os.link, os.unlink))
        and os.stat in os.supports_follow_symlinks
        and os.link in os.supports_follow_symlinks
    )


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode


def _open_directory(
    path: object,
) -> tuple[list[int], list[tuple[int, str, int, tuple[int, int, int]]]]:
    raw = os.fspath(path)
    if (
        type(raw) is not str
        or not raw
        or len(raw) > 4096
        or "\x00" in raw
        or not os.path.isabs(raw)
        or ".." in raw.split(os.sep)
    ):
        raise ValueError("invalid directory")
    components = [part for part in raw.split(os.sep) if part and part != "."]
    if not components or len(components) > 128:
        raise ValueError("invalid directory")
    descriptors = []
    chain = []
    try:
        if os.name == "nt":
            # Windows has no dir_fd: open the volume root, then walk the
            # remaining components one descriptor at a time.  Every component
            # keeps the same nofollow + by-handle identity check as POSIX.
            absolute = os.path.abspath(raw)
            drive, tail = os.path.splitdrive(absolute)
            anchor = drive + os.sep
            components = [part for part in tail.split(os.sep) if part and part != "."]
            if not components:
                raise ValueError("invalid directory")
            root = fdops.open_root(anchor, check_owner=False)
        else:
            components = [part for part in raw.split(os.sep) if part and part != "."]
            root = fdops.open(os.sep, os.O_RDONLY | fdops.O_DIRECTORY | fdops.O_NOFOLLOW)
        descriptors.append(root)
        parent = root
        for name in components:
            before = fdops.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError("directory must not be a symlink")
            fd = fdops.open(name, os.O_RDONLY | fdops.O_DIRECTORY | fdops.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(fd)
            identity = _identity(fdops.fstat(fd))
            if _identity(before) != identity:
                raise ValueError("directory changed")
            chain.append((parent, name, fd, identity))
            parent = fd
        return descriptors, chain
    except Exception:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                fdops.close(fd)
        raise


def _directory_matches(chain) -> bool:
    try:
        return all(
            _identity(fdops.stat(name, dir_fd=parent, follow_symlinks=False)) == identity
            and _identity(fdops.fstat(fd)) == identity
            for parent, name, fd, identity in chain
        )
    except OSError:
        return False


def _remove_owned_temp(directory: int, name: str, identity: tuple[int, int, int]) -> None:
    try:
        if _identity(fdops.stat(name, dir_fd=directory, follow_symlinks=False)) == identity:
            fdops.unlink(name, dir_fd=directory)
    except OSError:
        pass


def retain_preview_report(
    report: PreviewReport, *, directory: str | os.PathLike[str], filesystem: str
) -> PreviewRetentionResult:
    """Write one new report to an existing directory under the stated FS model."""
    if type(filesystem) is not str or filesystem != TRUSTED_LOCAL_POSIX or not _supported():
        return PreviewRetentionResult(
            "NOT_COMMITTED", "NOT_OBSERVED", reason="UNSUPPORTED_FILESYSTEM"
        )
    validated_report = _validated_report(report)
    if validated_report is None:
        return PreviewRetentionResult("NOT_COMMITTED", "NOT_OBSERVED", reason="INVALID_REPORT")
    try:
        descriptors, chain = _open_directory(directory)
    except Exception:
        return PreviewRetentionResult("NOT_COMMITTED", "NOT_OBSERVED", reason="INVALID_DIRECTORY")
    try:
        run_id = uuid.uuid4().hex
        temp_id = uuid.uuid4().hex
        if type(run_id) is not str or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("invalid run ID")
        if type(temp_id) is not str or _RUN_ID.fullmatch(temp_id) is None:
            raise ValueError("invalid temporary ID")
        name = f"preview-{run_id}.json"
        temp = f".preview-{run_id}-{temp_id}.tmp"
    except Exception:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                fdops.close(fd)
        return PreviewRetentionResult("NOT_COMMITTED", "NOT_OBSERVED", reason="RANDOM_UNAVAILABLE")
    directory_fd = descriptors[-1]
    temp_fd = None
    temp_identity = None
    attempted = False
    visible = False
    try:
        prefix = json.dumps(
            {"schema": _SCHEMA, "run_id": run_id}, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        payload = prefix[:-1] + b',"report":' + validated_report + b"}"
        if len(payload) > _LIMIT or not _directory_matches(chain):
            raise ValueError("report or directory invalid")
        temp_fd = fdops.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | fdops.O_NOFOLLOW, 0o600, dir_fd=directory_fd
        )
        temp_identity = _identity(fdops.fstat(temp_fd))
        offset = 0
        while offset < len(payload):
            written = fdops.write(temp_fd, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        fdops.fchmod(temp_fd, 0o400)
        temp_identity = _identity(fdops.fstat(temp_fd))
        fdops.fsync(temp_fd)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        attempted = True
        try:
            fdops.link(
                temp, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False
            )
        except FileExistsError:
            existing = fdops.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(existing) == temp_identity or not _directory_matches(chain):
                raise
            attempted = False
            return PreviewRetentionResult(
                "NOT_COMMITTED", "NOT_OBSERVED", run_id=run_id, reason="RUN_ID_EXISTS"
            )
        final_fd = fdops.open(name, os.O_RDONLY | fdops.O_NOFOLLOW, dir_fd=directory_fd)
        try:
            if _identity(fdops.fstat(final_fd)) != temp_identity:
                raise ValueError("readback identity mismatch")
            received = bytearray()
            while len(received) <= _LIMIT:
                part = fdops.read(final_fd, _LIMIT + 1 - len(received))
                if not part:
                    break
                received.extend(part)
            if bytes(received) != payload or not _directory_matches(chain):
                raise ValueError("readback mismatch")
        finally:
            fdops.close(final_fd)
        visible = True
        fdops.unlink(temp, dir_fd=directory_fd)
        temp_identity = None
        fdops.fsync(directory_fd)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return PreviewRetentionResult("COMMITTED", "VISIBLE", run_id=run_id)
    except Exception:
        return PreviewRetentionResult(
            "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            "VISIBLE" if visible else "NOT_OBSERVED",
            run_id=run_id,
            reason="IO_OR_STATE_ERROR",
        )
    finally:
        if temp_fd is not None:
            with contextlib.suppress(OSError):
                fdops.close(temp_fd)
        if temp_identity is not None:
            _remove_owned_temp(directory_fd, temp, temp_identity)
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                fdops.close(fd)
