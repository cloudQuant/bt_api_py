"""Explicit offline derivation of unverified hours review candidates.

Local digests, IDs and revalidation do not authenticate writers, sources or
association. Only a cooperating trusted local POSIX filesystem is supported;
ABA, hostile replacement and power-loss durability remain unproven. A commit
with unknown outcome must be checked using the same ID, never blindly retried.
No network, account access, scheduler, publication or CURRENT update occurs.
"""

from __future__ import annotations

import argparse
import contextlib
from bt_api_py._calendar_compat import fcntl
import json
import os
from bt_api_py import _calendar_fdops as fdops
import re
import stat
import sys
from dataclasses import asdict
from datetime import date
from urllib.parse import unquote

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _supported,
)

_SCHEMA = "bt_api.calendar-hours-review-candidate/1"
_LIMIT = 4 * 1024 * 1024
_SELECTION_LIMIT = 512 * 1024
_SELECTION_FIELDS = {
    "venue",
    "kind",
    "page_product_name",
    "product",
    "contracts",
    "valid_from",
    "valid_to",
}
_LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])/(?!/)[^\s/]+"
    r"|(?<![A-Za-z0-9:/])//[^\s/]+/[^\s/]+"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"|\\\\[^\\\s]+\\[^\\\s]+"
    r"|(?<![A-Za-z0-9+./:-])file:",
    re.IGNORECASE,
)


def _sensitive(body, root):
    if detail._sensitive(body, root):
        return True
    pending = [body]
    while pending:
        item = pending.pop()
        if type(item) is str:
            # Cover ordinary and twice-percent-encoded paths, not arbitrary encodings.
            for _ in range(3):
                if _LOCAL_PATH.search(item):
                    return True
                item = unquote(item)
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return False


def _selections(data):
    from bt_api_ctp.calendar_shfe_hours_candidates import HoursSelection

    if type(data) is not list or not 1 <= len(data) <= 128:
        raise ValueError("invalid selections")
    selected = []
    for row in data:
        if (
            type(row) is not dict
            or set(row) != _SELECTION_FIELDS
            or type(row["contracts"]) is not list
        ):
            raise ValueError("invalid selection")
        dates = []
        for key in ("valid_from", "valid_to"):
            value = row[key]
            if type(value) is not str:
                raise ValueError("invalid date")
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("invalid date")
            dates.append(parsed)
        selected.append(
            HoursSelection(
                row["venue"],
                row["kind"],
                row["page_product_name"],
                row["product"],
                tuple(row["contracts"]),
                *dates,
            )
        )
    return tuple(selected)


def _derive(source, selections, candidate_id):
    from bt_api_ctp.calendar_candidate_merge import PartialMergeResult
    from bt_api_ctp.calendar_hours_merge import merge_hours_candidates
    from bt_api_ctp.calendar_shfe_candidates import UnresolvedNight
    from bt_api_ctp.calendar_shfe_hours_candidates import convert_shfe_hours
    from bt_api_ctp.calendar_supplement import _validate

    payload = source["payload"]
    if (
        payload["input_scope"] != "YAML_SHFE_PARTIAL"
        or payload["holiday_status"] != "CAPTURED_UNVERIFIED"
        or payload["unresolved_nights_status"] != "CAPTURED_UNVERIFIED"
        or payload["hours_page_status"] != "CAPTURED_UNVERIFIED"
    ):
        raise ValueError("required source unavailable")
    page = inbox._page_from_data(payload["hours_page"])
    nights = tuple(
        UnresolvedNight(
            item["holiday"],
            date.fromisoformat(item["local_date"]),
            item["source_id"],
            item["raw_text"],
        )
        for item in payload["unresolved_nights"]
    )
    partial = PartialMergeResult(
        "PARTIAL",
        _validate(payload["content"]),
        nights,
        remaining_checks=tuple(payload["remaining_checks"]),
    )
    hours = convert_shfe_hours(page, selections=_selections(selections))
    if hours.status != "CANDIDATE":
        raise ValueError("invalid hours selection")
    merged = merge_hours_candidates(partial=partial, hours=hours)
    if merged.status != "PARTIAL":
        raise ValueError("hours merge rejected")
    holiday_nights = [asdict(item) for item in merged.holiday_unresolved_nights]
    for item in holiday_nights:
        item["local_date"] = item["local_date"].isoformat()
    body = {
        "schema": _SCHEMA,
        "candidate_id": candidate_id,
        "source_candidate_id": source["candidate_id"],
        "source_candidate_schema": source["schema"],
        "source_payload_sha256": source["payload_sha256"],
        "selections": selections,
        "content": merged.supplement.content,
        "holiday_unresolved_nights": holiday_nights,
        "hours_unresolved_nights": [asdict(item) for item in merged.hours_unresolved_nights],
        "page_notes": list(merged.page_notes),
        "page_review_reasons": list(merged.page_review_reasons),
        "remaining_checks": list(merged.remaining_checks),
        "status": "PARTIAL",
        "verification": "UNVERIFIED",
        "declarations": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "source_association": "NOT_PROVEN",
        "binding": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }
    return json.loads(storage._json(body), object_pairs_hook=runs._unique_pairs)


def _open(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                fdops.mkdir("hours-review-candidates", 0o700, dir_fd=parent)
        info = fdops.stat("hours-review-candidates", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != fdops.geteuid():
            raise ValueError("invalid directory")
        fd = fdops.open(
            "hours-review-candidates", os.O_RDONLY | fdops.O_DIRECTORY | fdops.O_NOFOLLOW, dir_fd=parent
        )
        fds.append(fd)
        if _identity(fdops.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "hours-review-candidates", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _valid(root, filesystem, candidate_id):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and runs._id(candidate_id)
    )


def build_once(*, root, source_candidate_id, candidate_id, selections, filesystem, emit):
    """Derive once, preserving interrupted caller control and the allocated ID."""
    try:
        return _build_once(
            root=root,
            source_candidate_id=source_candidate_id,
            candidate_id=candidate_id,
            selections=selections,
            filesystem=filesystem,
            emit=emit,
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        with contextlib.suppress(Exception):
            exc.candidate_id = candidate_id if runs._id(candidate_id) else ""
            exc.commit_status = "COMMIT_UNKNOWN"
        raise


def _build_once(*, root, source_candidate_id, candidate_id, selections, filesystem, emit):
    """Emit the ID before any write. No automatic retry or resume."""
    if not _valid(root, filesystem, candidate_id) or not runs._id(source_candidate_id):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "candidate_id": ""}
    emit({"event": "HOURS_CANDIDATE_ID_ALLOCATED", "candidate_id": candidate_id})
    result = {
        "status": "NOT_COMMITTED",
        "reason": "INPUT_UNAVAILABLE",
        "candidate_id": candidate_id,
    }
    try:
        if len(storage._json(selections)) > _SELECTION_LIMIT:
            return result
        source = inbox.read_candidate(
            root=root, candidate_id=source_candidate_id, filesystem=filesystem
        )
        body = _derive(source, selections, candidate_id)
        if _sensitive(body, root):
            return result
        encoded = storage._envelope(body)
        if len(encoded) > _LIMIT:
            return result
        fds, chain = _open(root, True)
    except Exception:
        return result
    lock = None
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        current = inbox.read_candidate(
            root=root, candidate_id=source_candidate_id, filesystem=filesystem
        )
        if current != source:
            return result
        storage._check_lock(fds[-1], chain, lock, lock_id)
        status = storage._write(fds[-1], chain, f"candidate-{candidate_id}.json", encoded, _LIMIT)
        return {
            "status": status,
            "reason": "NONE"
            if status == "COMMITTED"
            else "WRITE_UNCERTAIN"
            if status == "COMMIT_UNKNOWN"
            else "WRITE_REJECTED",
            "candidate_id": candidate_id,
        }
    except Exception:
        return {
            "status": "COMMIT_UNKNOWN",
            "reason": "INTERRUPTED_OR_IO_UNKNOWN",
            "candidate_id": candidate_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                fdops.close(lock)
        runs._close(fds)


def show(*, root, candidate_id, filesystem):
    """Read and recompute the candidate against its retained original."""
    result = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE",
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
    }
    if not _valid(root, filesystem, candidate_id):
        return result
    try:
        fds, chain = _open(root, False)
    except Exception:
        return result
    try:
        raw = storage._read(fds[-1], chain, f"candidate-{candidate_id}.json", _LIMIT)
        if raw is None:
            return result
        body, _ = storage._unwrap(raw)
        if body.get("schema") != _SCHEMA or body.get("candidate_id") != candidate_id:
            return result
        source = inbox.read_candidate(
            root=root, candidate_id=body.get("source_candidate_id"), filesystem=filesystem
        )
        if _derive(source, body.get("selections"), candidate_id) != body or _sensitive(body, root):
            return result
        if not _directory_matches(chain):
            return result
        return {
            "status": "VISIBLE_UNAUTHENTICATED",
            "reason": "UNVERIFIED_REVIEW",
            "candidate_id": candidate_id,
            "candidate": body,
        }
    except Exception:
        return result
    finally:
        runs._close(fds)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name in ("build", "show"):
        command = sub.add_parser(name)
        command.add_argument("--calendar-root", required=True)
        command.add_argument("--candidate-id", required=True)
        command.add_argument("--trusted-local-posix", action="store_true")
        if name == "build":
            command.add_argument("--source-candidate-id", required=True)
    try:
        args = parser.parse_args(argv)
        if not args.trusted_local_posix or not _valid(
            args.calendar_root, TRUSTED_LOCAL_POSIX, args.candidate_id
        ):
            raise ValueError("invalid arguments")
        if args.command == "show":
            result = show(
                root=args.calendar_root,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
            )
        else:
            raw = sys.stdin.buffer.read(_SELECTION_LIMIT + 1)
            if len(raw) > _SELECTION_LIMIT:
                raise ValueError("selection limit")
            selections = json.loads(raw, object_pairs_hook=runs._unique_pairs)
            _selections(selections)

            def emit(value):
                print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)

            result = build_once(
                root=args.calendar_root,
                source_candidate_id=args.source_candidate_id,
                candidate_id=args.candidate_id,
                selections=selections,
                filesystem=TRUSTED_LOCAL_POSIX,
                emit=emit,
            )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return (
            10
            if result["status"] in {"COMMITTED", "VISIBLE_UNAUTHENTICATED"}
            else 25
            if result["status"] in {"UNKNOWN", "COMMIT_UNKNOWN"}
            else 2
        )
    except Exception:
        print('{"status":"NOT_COMMITTED","reason":"INVALID_ARGUMENTS"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
