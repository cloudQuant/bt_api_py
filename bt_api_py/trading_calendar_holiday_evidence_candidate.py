"""Derive a partial SHFE holiday candidate from retained public evidence.

Only a cooperating trusted local POSIX filesystem is supported. Digests do not
authenticate the source or grant publication authority. A result of unknown
commit status must be inspected using its original ID. No network or CURRENT
access occurs. Python callers requesting source text must use public data only.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import sys
from copy import deepcopy
from dataclasses import asdict

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_source_evidence as evidence
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _supported,
)

_SCHEMA = "bt_api.calendar-holiday-evidence-candidate/1"
_LIMIT = 4 * 1024 * 1024
_URL = "https://www.shfe.com.cn/services/calenderandholidays/holiday/"


def _valid(root, filesystem, candidate_id, evidence_id):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and runs._id(candidate_id)
        and runs._id(evidence_id)
    )


def _open(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("holiday-evidence-candidates", 0o700, dir_fd=parent)
        info = os.stat("holiday-evidence-candidates", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("invalid directory")
        fd = os.open(
            "holiday-evidence-candidates",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "holiday-evidence-candidates", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _derive(source, candidate_id):
    from bt_api_ctp.calendar_shfe_candidates import convert_shfe_holiday
    from bt_api_ctp.calendar_shfe_holiday import parse_shfe_holiday

    if (
        type(source) is not dict
        or source.get("status") != "VISIBLE_UNAUTHENTICATED"
        or source.get("exchange", "SHFE") != "SHFE"
        or source.get("kind") != "holiday"
        or source.get("transport") != "HTTP_OK"
        or source.get("page_status") != "CANDIDATE"
        or not runs._id(source.get("evidence_id"))
        or type(source.get("raw_bytes")) is not bytes
        or type(source.get("source_sha256")) is not str
        or hashlib.sha256(source["raw_bytes"]).hexdigest() != source["source_sha256"]
        or type(source.get("payload_sha256")) is not str
        or len(source["payload_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in source["payload_sha256"])
    ):
        raise ValueError("evidence unavailable")
    page = parse_shfe_holiday(source_id=_URL, html=source["raw_bytes"])
    if page.status != "CANDIDATE" or page.sha256 != source["source_sha256"]:
        raise ValueError("invalid holiday page")
    converted = convert_shfe_holiday(page)
    if converted.status != "CANDIDATE" or converted.supplement is None:
        raise ValueError("invalid conversion")
    nights = []
    for night in converted.unresolved_nights:
        row = asdict(night)
        row["local_date"] = night.local_date.isoformat()
        nights.append(row)
    body = {
        "schema": _SCHEMA,
        "candidate_id": candidate_id,
        "evidence_id": source["evidence_id"],
        "evidence_payload_sha256": source["payload_sha256"],
        "source_sha256": source["source_sha256"],
        "source_id": source.get("candidate_source_id"),
        "year": page.year,
        "content": converted.supplement.content,
        "unresolved_nights": nights,
        "status": "PARTIAL",
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "utc_coverage": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }
    if body["source_id"] != f"shfe.holiday.{page.year}.{page.sha256}":
        raise ValueError("source identity mismatch")
    return json.loads(storage._json(body), object_pairs_hook=runs._unique_pairs)


def build_once(*, root, evidence_id, candidate_id, filesystem, emit):
    """Save one derived candidate; callers must not retry uncertain writes."""
    if not _valid(root, filesystem, candidate_id, evidence_id) or not callable(emit):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "candidate_id": ""}
    try:
        emit({"event": "HOLIDAY_CANDIDATE_ID_ALLOCATED", "candidate_id": candidate_id})
        source = evidence.show(
            root=root, evidence_id=evidence_id, filesystem=filesystem, include_public_bytes=True
        )
        body = _derive(source, candidate_id)
        if detail._sensitive(body, root):
            raise ValueError("sensitive input")
        encoded = storage._envelope(body)
        if len(encoded) > _LIMIT:
            raise ValueError("candidate too large")
        fds, chain = _open(root, True)
    except KeyboardInterrupt:
        return {"status": "NOT_COMMITTED", "reason": "INTERRUPTED", "candidate_id": candidate_id}
    except Exception:
        return {
            "status": "NOT_COMMITTED",
            "reason": "INPUT_UNAVAILABLE",
            "candidate_id": candidate_id,
        }
    lock = None
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        current = evidence.show(
            root=root, evidence_id=evidence_id, filesystem=filesystem, include_public_bytes=True
        )
        if current != source:
            return {
                "status": "NOT_COMMITTED",
                "reason": "SOURCE_CHANGED",
                "candidate_id": candidate_id,
            }
        storage._check_lock(fds[-1], chain, lock, lock_id)
        status = storage._write(fds[-1], chain, f"candidate-{candidate_id}.json", encoded, _LIMIT)
        return {"status": status, "candidate_id": candidate_id}
    except (Exception, KeyboardInterrupt):
        return {
            "status": "COMMIT_UNKNOWN",
            "reason": "WRITE_UNCERTAIN",
            "candidate_id": candidate_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def show(*, root, candidate_id, filesystem, include_source_text=False):
    """Recompute from retained evidence; opt-in source text is public material."""
    unknown = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE",
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
    }
    if (
        not _valid(root, filesystem, candidate_id, candidate_id)
        or type(include_source_text) is not bool
    ):
        return unknown
    try:
        fds, chain = _open(root, False)
    except Exception:
        return unknown
    try:
        raw = storage._read(fds[-1], chain, f"candidate-{candidate_id}.json", _LIMIT)
        if raw is None:
            return unknown
        body, digest = storage._unwrap(raw)
        if body.get("schema") != _SCHEMA or body.get("candidate_id") != candidate_id:
            return unknown
        source = evidence.show(
            root=root,
            evidence_id=body.get("evidence_id"),
            filesystem=filesystem,
            include_public_bytes=True,
        )
        if (
            _derive(source, candidate_id) != body
            or detail._sensitive(body, root)
            or not _directory_matches(chain)
        ):
            return unknown
        visible_body = deepcopy(body)
        if not include_source_text:
            for item in visible_body["unresolved_nights"]:
                item.pop("raw_text", None)
        result = {
            "status": "VISIBLE_UNAUTHENTICATED",
            "candidate_id": candidate_id,
            "payload_sha256": digest,
            "candidate": visible_body,
        }
        if include_source_text:
            result["source_text"] = source.get("utf8_text")
            result["source_text_status"] = (
                "LOSSLESS_UNAUTHENTICATED" if source.get("utf8_text") is not None else "UNAVAILABLE"
            )
        return result
    except Exception:
        return unknown
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
            command.add_argument("--evidence-id", required=True)
    try:
        args = parser.parse_args(argv)
        if not args.trusted_local_posix:
            raise ValueError("filesystem declaration required")
        if args.command == "build":
            result = build_once(
                root=args.calendar_root,
                evidence_id=args.evidence_id,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
                emit=lambda x: print(json.dumps(x), flush=True),
            )
        else:
            result = show(
                root=args.calendar_root,
                candidate_id=args.candidate_id,
                filesystem=TRUSTED_LOCAL_POSIX,
            )
            if result["status"] == "VISIBLE_UNAUTHENTICATED":
                body = result.pop("candidate")
                result.update(
                    {
                        "year": body["year"],
                        "closed_count": len(body["content"]["trading_days"]),
                        "unresolved_night_count": len(body["unresolved_nights"]),
                        "candidate_status": "PARTIAL",
                        "publication": "NOT_AUTHORIZED",
                    }
                )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 10 if result["status"] in {"COMMITTED", "VISIBLE_UNAUTHENTICATED"} else 25
    except KeyboardInterrupt:
        print('{"status":"COMMIT_UNKNOWN","reason":"INTERRUPTED"}')
        return 130
    except Exception:
        print('{"status":"NOT_COMMITTED","reason":"INVALID_ARGUMENTS"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
