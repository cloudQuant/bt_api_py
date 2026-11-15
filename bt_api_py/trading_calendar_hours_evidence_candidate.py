"""Derive a partial SHFE or INE hours candidate from retained observations.

Inputs must remain available for later revalidation. Selections are caller
declarations; neither retained bytes nor matching digests authenticate them.
Only cooperating trusted local POSIX writers are supported. This module does
not publish, access CURRENT, use a network, or establish trading authority.
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
from dataclasses import asdict
from datetime import date

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_holiday_evidence_candidate as holidays
from bt_api_py import trading_calendar_hours_candidate as legacy
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_source_evidence as evidence
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _supported,
)

_SCHEMA = "bt_api.calendar-hours-evidence-candidate/1"
_SCHEMA_INE = "bt_api.calendar-hours-evidence-candidate/2"
_LIMIT = 4 * 1024 * 1024
_URL = "https://www.shfe.com.cn/services/calenderandholidays/tradinghours/"


def _valid(root, filesystem, *ids):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and all(runs._id(value) for value in ids)
    )


def _open(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("hours-evidence-candidates", 0o700, dir_fd=parent)
        info = os.stat("hours-evidence-candidates", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("invalid directory")
        fd = os.open(
            "hours-evidence-candidates", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "hours-evidence-candidates", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _inputs(root, evidence_id, holiday_id, filesystem):
    hours = evidence.show(
        root=root, evidence_id=evidence_id, filesystem=filesystem, include_public_bytes=True
    )
    holiday = holidays.show(
        root=root, candidate_id=holiday_id, filesystem=filesystem, include_source_text=True
    )
    holiday_evidence = None
    candidate = holiday.get("candidate") if type(holiday) is dict else None
    if type(candidate) is dict and candidate.get("exchange") == "INE":
        holiday_evidence = evidence.show(
            root=root,
            evidence_id=candidate.get("evidence_id"),
            filesystem=filesystem,
            include_public_bytes=True,
        )
    return hours, holiday, holiday_evidence


def _derive(hours, holiday, holiday_evidence, selections, candidate_id):
    from bt_api_ctp.calendar_candidate_merge import PartialMergeResult
    from bt_api_ctp.calendar_hours_merge import merge_hours_candidates
    from bt_api_ctp.calendar_shfe_candidates import UnresolvedNight
    from bt_api_ctp.calendar_shfe_hours_candidates import convert_shfe_hours
    from bt_api_ctp.calendar_shfe_page import parse_shfe_page
    from bt_api_ctp.calendar_supplement import _validate

    selected = legacy._selections(selections)
    if (
        type(hours) is not dict
        or hours.get("status") != "VISIBLE_UNAUTHENTICATED"
        or hours.get("exchange", "SHFE") not in {"SHFE", "INE"}
        or hours.get("kind") != "hours"
        or hours.get("transport") != "HTTP_OK"
        or hours.get("page_status") not in {"CANDIDATE", "NEEDS_REVIEW"}
        or not runs._id(hours.get("evidence_id"))
        or type(hours.get("raw_bytes")) is not bytes
        or hashlib.sha256(hours["raw_bytes"]).hexdigest() != hours.get("source_sha256")
    ):
        raise ValueError("hours evidence unavailable")
    if (
        type(holiday) is not dict
        or holiday.get("status") != "VISIBLE_UNAUTHENTICATED"
        or not runs._id(holiday.get("candidate_id"))
        or type(holiday.get("candidate")) is not dict
    ):
        raise ValueError("holiday candidate unavailable")
    source = holiday["candidate"]
    exchange = hours.get("exchange", "SHFE")
    if any(item.venue != exchange for item in selected):
        raise ValueError("wrong venue")
    if (
        source.get("schema")
        != f"bt_api.calendar-holiday-evidence-candidate/{'1' if exchange == 'SHFE' else '2'}"
        or (exchange == "INE" and source.get("exchange") != "INE")
        or source.get("status") != "PARTIAL"
        or source.get("publication") != "NOT_AUTHORIZED"
    ):
        raise ValueError("wrong holiday venue")
    if exchange == "INE":
        from bt_api_ctp.calendar_ine_holiday import SOURCE_URL as HOLIDAY_URL
        from bt_api_ctp.calendar_ine_holiday import parse_ine_holiday
        from bt_api_ctp.calendar_ine_hours_candidates import merge_ine_holiday_hours
        from bt_api_ctp.calendar_ine_hours_fetch import SOURCE as HOURS_URL

        if (
            type(holiday_evidence) is not dict
            or holiday_evidence.get("status") != "VISIBLE_UNAUTHENTICATED"
            or holiday_evidence.get("exchange") != "INE"
            or holiday_evidence.get("kind") != "holiday"
            or holiday_evidence.get("transport") != "HTTP_OK"
            or holiday_evidence.get("page_status") != "CANDIDATE"
            or holiday_evidence.get("evidence_id") != source.get("evidence_id")
            or holiday_evidence.get("payload_sha256") != source.get("evidence_payload_sha256")
            or holiday_evidence.get("source_sha256") != source.get("source_sha256")
            or type(holiday_evidence.get("raw_bytes")) is not bytes
            or hashlib.sha256(holiday_evidence["raw_bytes"]).hexdigest()
            != source.get("source_sha256")
        ):
            raise ValueError("holiday evidence unavailable")
    else:
        HOURS_URL = _URL
    page = parse_shfe_page(source_id=HOURS_URL, html=hours["raw_bytes"])
    if page.status != hours["page_status"] or page.sha256 != hours["source_sha256"]:
        raise ValueError("hours page changed")
    if exchange == "SHFE":
        nights = tuple(
            UnresolvedNight(
                row["holiday"],
                date.fromisoformat(row["local_date"]),
                row["source_id"],
                row["raw_text"],
            )
            for row in source["unresolved_nights"]
        )
        partial = PartialMergeResult("PARTIAL", _validate(source["content"]), nights)
        converted = convert_shfe_hours(page, selections=selected)
        merged = merge_hours_candidates(partial=partial, hours=converted)
        if converted.status != "CANDIDATE":
            raise ValueError("invalid hours")
        remaining = list(merged.remaining_checks)
    else:
        holiday_page = parse_ine_holiday(source_id=HOLIDAY_URL, html=holiday_evidence["raw_bytes"])
        merged = merge_ine_holiday_hours(
            holiday=holiday_page,
            holiday_html=holiday_evidence["raw_bytes"],
            page=page,
            hours_html=hours["raw_bytes"],
            selections=selected,
        )
        remaining = list(PartialMergeResult("PARTIAL").remaining_checks) + [
            "OTHER_SESSIONS_NOT_CHECKED",
            "HOURS_NIGHT_MAPPING_UNRESOLVED",
        ]
    if merged.status != "PARTIAL" or merged.supplement is None:
        raise ValueError("invalid hours merge")
    holiday_nights = [asdict(item) for item in merged.holiday_unresolved_nights]
    for item in holiday_nights:
        item["local_date"] = item["local_date"].isoformat()
    if exchange == "INE" and (
        merged.supplement.content["trading_days"] != source["content"]["trading_days"]
        or holiday_nights != source["unresolved_nights"]
    ):
        raise ValueError("holiday candidate differs from source")
    body = {
        "schema": _SCHEMA if exchange == "SHFE" else _SCHEMA_INE,
        "candidate_id": candidate_id,
        "hours_evidence_id": hours["evidence_id"],
        "hours_evidence_payload_sha256": hours["payload_sha256"],
        "hours_source_sha256": hours["source_sha256"],
        "holiday_candidate_id": holiday["candidate_id"],
        "holiday_candidate_payload_sha256": holiday["payload_sha256"],
        "holiday_evidence_id": source["evidence_id"],
        "holiday_evidence_payload_sha256": source["evidence_payload_sha256"],
        "holiday_source_sha256": source["source_sha256"],
        "selections": selections,
        "content": merged.supplement.content,
        "holiday_unresolved_nights": holiday_nights,
        "hours_unresolved_nights": [asdict(item) for item in merged.hours_unresolved_nights],
        "page_notes": list(merged.page_notes),
        "page_review_reasons": list(merged.page_review_reasons),
        "remaining_checks": remaining,
        "status": "PARTIAL",
        "declarations": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "source_association": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }
    if exchange == "INE":
        body["exchange"] = "INE"
    return json.loads(storage._json(body), object_pairs_hook=runs._unique_pairs)


def build_once(
    *, root, evidence_id, holiday_candidate_id, candidate_id, selections, filesystem, emit
):
    if not _valid(
        root, filesystem, evidence_id, holiday_candidate_id, candidate_id
    ) or not callable(emit):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS", "candidate_id": ""}
    try:
        emit({"event": "HOURS_CANDIDATE_ID_ALLOCATED", "candidate_id": candidate_id})
        selection_bytes = storage._json(selections)
        if len(selection_bytes) > legacy._SELECTION_LIMIT:
            raise ValueError("selection limit")
        selections = json.loads(selection_bytes, object_pairs_hook=runs._unique_pairs)
        sources = _inputs(root, evidence_id, holiday_candidate_id, filesystem)
        body = _derive(*sources, selections, candidate_id)
        if legacy._sensitive(body, root):
            raise ValueError("sensitive input")
        encoded = storage._envelope(body)
        if len(encoded) > _LIMIT:
            raise ValueError("candidate limit")
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
        if _inputs(root, evidence_id, holiday_candidate_id, filesystem) != sources:
            return {
                "status": "NOT_COMMITTED",
                "reason": "SOURCE_CHANGED",
                "candidate_id": candidate_id,
            }
        storage._check_lock(fds[-1], chain, lock, lock_id)
        status = storage._write(fds[-1], chain, f"candidate-{candidate_id}.json", encoded, _LIMIT)
        return {"status": status, "candidate_id": candidate_id}
    except BaseException:
        return {
            "status": "COMMIT_UNKNOWN",
            "reason": "WRITE_UNCERTAIN",
            "candidate_id": candidate_id,
        }
    finally:
        try:
            if lock is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(lock)
            runs._close(fds)
        except BaseException:
            return {
                "status": "COMMIT_UNKNOWN",
                "reason": "CLEANUP_UNCERTAIN",
                "candidate_id": candidate_id,
            }


def show(*, root, candidate_id, filesystem, include_source_text=False):
    unknown = {
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE",
        "candidate_id": candidate_id if runs._id(candidate_id) else "",
    }
    if not _valid(root, filesystem, candidate_id) or type(include_source_text) is not bool:
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
        if (
            body.get("schema") not in {_SCHEMA, _SCHEMA_INE}
            or body.get("candidate_id") != candidate_id
        ):
            return unknown
        sources = _inputs(
            root, body.get("hours_evidence_id"), body.get("holiday_candidate_id"), filesystem
        )
        if (
            _derive(*sources, body.get("selections"), candidate_id) != body
            or legacy._sensitive(body, root)
            or not _directory_matches(chain)
        ):
            return unknown
        result = {
            "status": "VISIBLE_UNAUTHENTICATED",
            "candidate_id": candidate_id,
            "payload_sha256": digest,
            "candidate": body,
            "page_notes_count": len(body["page_notes"]),
            "page_notes_status": "INCLUDED" if include_source_text else "REDACTED",
        }
        if include_source_text:
            result["hours_source_text"] = sources[0].get("utf8_text")
            result["holiday_source_text"] = sources[1].get("source_text")
        else:
            for item in body["holiday_unresolved_nights"]:
                item.pop("raw_text", None)
            for item in body["hours_unresolved_nights"]:
                for stage in item["stages"]:
                    stage.pop("raw_line", None)
            body["page_notes"] = []
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
            command.add_argument("--holiday-candidate-id", required=True)
    try:
        args = parser.parse_args(argv)
        if not args.trusted_local_posix:
            raise ValueError("filesystem declaration required")
        if args.command == "build":
            raw = sys.stdin.buffer.read(legacy._SELECTION_LIMIT + 1)
            if len(raw) > legacy._SELECTION_LIMIT:
                raise ValueError("selection limit")
            selections = json.loads(raw, object_pairs_hook=runs._unique_pairs)
            result = build_once(
                root=args.calendar_root,
                evidence_id=args.evidence_id,
                holiday_candidate_id=args.holiday_candidate_id,
                candidate_id=args.candidate_id,
                selections=selections,
                filesystem=TRUSTED_LOCAL_POSIX,
                emit=lambda value: print(json.dumps(value), flush=True),
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
                        "closed_count": len(body["content"]["trading_days"]),
                        "hours_count": len(body["content"]["trading_hours"]),
                        "holiday_unresolved_count": len(body["holiday_unresolved_nights"]),
                        "hours_unresolved_count": len(body["hours_unresolved_nights"]),
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
