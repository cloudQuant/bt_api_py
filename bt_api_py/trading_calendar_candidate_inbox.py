"""Experimental, explicitly requested retention of unverified preview candidates.

Candidates are incomplete review material, never authorization or source proof.
This API never reads CURRENT or publishes. Names are no-overwrite by this API;
0600 files can still be altered by their owner. The caller asserts a trusted,
cooperating local POSIX filesystem; hostile writers, Windows, network filesystems
and power-loss durability are not proved. Ordinary preview output contains only
finite metadata. The separate ``show`` command explicitly prints candidate data.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _remove_owned_temp,
    _supported,
)

_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SCHEMA = "bt_api.calendar-preview-candidate/1"
_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class CandidateRetention:
    status: str
    reason: str
    candidate_id: str


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _page_to_data(page):
    if page is None:
        return None
    from bt_api_ctp.calendar_shfe_hours_candidates import _recheck_page

    _recheck_page(page)
    return asdict(page)


def _page_from_data(value):
    if value is None:
        return None
    from bt_api_ctp.calendar_shfe_hours_candidates import _recheck_page
    from bt_api_ctp.calendar_shfe_page import PageCandidate, ProductCandidate, StageCandidate

    if (
        type(value) is not dict
        or set(value)
        != {
            "status",
            "source_id",
            "sha256",
            "products",
            "raw_notes",
            "review_reasons",
            "verification",
        }
        or type(value["products"]) is not list
    ):
        raise ValueError("invalid page")
    products = []
    for item in value["products"]:
        if (
            type(item) is not dict
            or set(item) != {"kind", "name", "raw_product_cell", "raw_time_cell", "stages"}
            or type(item["stages"]) is not list
        ):
            raise ValueError("invalid product")
        stages = tuple(StageCandidate(**stage) for stage in item["stages"])
        products.append(
            ProductCandidate(
                item["kind"], item["name"], item["raw_product_cell"], item["raw_time_cell"], stages
            )
        )
    if type(value["raw_notes"]) is not list or type(value["review_reasons"]) is not list:
        raise ValueError("invalid page notes")
    page = PageCandidate(
        value["status"],
        value["source_id"],
        value["sha256"],
        tuple(products),
        tuple(value["raw_notes"]),
        tuple(value["review_reasons"]),
        value["verification"],
    )
    _recheck_page(page)
    return page


def _validate_source_checks(checks):
    from bt_api_py.trading_calendar_preview import _PAGE, _TRANSPORT

    if type(checks) is not dict or set(checks) != {
        "hours",
        "holiday",
        "holiday_conversion_status",
        "merge_status",
        "merge_reason",
        "normalization_status",
    }:
        raise ValueError("invalid source checks")
    extra = {"CTP_UNAVAILABLE", "COLLECTOR_ERROR", "COLLECTOR_PROTOCOL_ERROR"}
    allowed = _PAGE | _TRANSPORT | extra
    reasons = {
        "NOT_REQUESTED",
        "NOT_ATTEMPTED",
        "CANDIDATE",
        "SHFE_NOT_VERIFIED",
        "RETRY_LIMIT_REACHED",
        "RETRY_BUDGET_EXHAUSTED",
        "BUDGET_EXHAUSTED",
        "CHILD_CLEANUP_UNCERTAIN",
    }
    for kind in ("hours", "holiday"):
        item = checks[kind]
        if (
            type(item) is not dict
            or set(item) != {"status", "reason", "attempts"}
            or item["status"] not in allowed | {"NOT_REQUESTED", "NOT_ATTEMPTED"}
            or item["reason"] not in reasons
            or type(item["attempts"]) is not list
            or len(item["attempts"]) > 3
        ):
            raise ValueError("invalid source summary")
        attempts = item["attempts"]
        if not attempts and (item["status"], item["reason"]) not in {
            ("NOT_REQUESTED", "NOT_REQUESTED"),
            ("NOT_ATTEMPTED", "NOT_ATTEMPTED"),
        }:
            raise ValueError("missing source attempts")
        if attempts and (
            item["reason"] in {"NOT_REQUESTED", "NOT_ATTEMPTED"}
            or type(attempts[-1]) is not dict
            or item["status"] != attempts[-1].get("source_status")
        ):
            raise ValueError("source status mismatch")
        for number, attempt in enumerate(attempts, 1):
            if (
                type(attempt) is not dict
                or set(attempt)
                != {
                    "number",
                    "transport_status",
                    "source_status",
                    "verification",
                    "product_count",
                    "parse_status",
                }
                or type(attempt["number"]) is not int
                or attempt["number"] != number
                or type(attempt["product_count"]) is not int
                or not 0 <= attempt["product_count"] <= 1024
                or (kind == "holiday" and attempt["product_count"] != 0)
                or attempt["verification"] != "UNVERIFIED"
                or attempt["transport_status"] not in _TRANSPORT | extra
                or attempt["source_status"] not in allowed
                or attempt["parse_status"]
                not in _PAGE | {"NOT_ATTEMPTED", "PARSER_ERROR", "UNKNOWN"}
            ):
                raise ValueError("invalid source attempt")
            if attempt["transport_status"] == "HTTP_OK":
                if (
                    attempt["source_status"] not in _PAGE
                    or attempt["parse_status"] != attempt["source_status"]
                ):
                    raise ValueError("invalid successful transport")
            elif (
                attempt["source_status"] != attempt["transport_status"]
                or attempt["product_count"] != 0
            ):
                raise ValueError("invalid failed transport")
            else:
                transport = attempt["transport_status"]
                if transport == "PARSER_ERROR":
                    expected_parse = "PARSER_ERROR"
                elif (
                    transport
                    in {
                        "CHILD_ERROR",
                        "CHILD_TIMEOUT",
                        "CHILD_CLEANUP_UNCERTAIN",
                        "RESULT_TOO_LARGE",
                    }
                    | extra
                ):
                    expected_parse = "UNKNOWN"
                else:
                    expected_parse = "NOT_ATTEMPTED"
                if attempt["parse_status"] != expected_parse:
                    raise ValueError("failed transport has invalid parse status")
        if attempts:
            last = attempts[-1]
            if last["transport_status"] in {"NETWORK_TIMEOUT", "CHILD_TIMEOUT"}:
                valid_reasons = {"RETRY_LIMIT_REACHED", "RETRY_BUDGET_EXHAUSTED"}
            elif last["transport_status"] == "CHILD_CLEANUP_UNCERTAIN":
                valid_reasons = {"CHILD_CLEANUP_UNCERTAIN"}
            elif last["source_status"] == "CANDIDATE":
                if kind == "holiday" and checks["holiday_conversion_status"] != "CANDIDATE":
                    valid_reasons = {"SHFE_NOT_VERIFIED"}
                else:
                    valid_reasons = {"CANDIDATE"}
            else:
                valid_reasons = {"SHFE_NOT_VERIFIED"}
            if item["reason"] not in valid_reasons:
                raise ValueError("source reason disagrees with final attempt")
    if (
        checks["holiday_conversion_status"]
        not in {
            "NOT_ATTEMPTED",
            "CANDIDATE",
            "INVALID_CANDIDATE",
            "CONVERTER_UNAVAILABLE",
            "CONVERSION_ERROR",
            "CONVERSION_PROTOCOL_ERROR",
        }
        or checks["merge_status"]
        not in {
            "NOT_ATTEMPTED",
            "PARTIAL",
            "MERGE_ERROR",
            "MERGE_UNAVAILABLE",
            "MERGE_PROTOCOL_ERROR",
        }
        or checks["merge_reason"] != "NONE"
        or checks["normalization_status"] not in {"NOT_ATTEMPTED", "NORMALIZED"}
    ):
        raise ValueError("invalid processing status")
    hours = checks["hours"]
    holiday = checks["holiday"]
    conversion = checks["holiday_conversion_status"]
    merge = checks["merge_status"]
    normalized = checks["normalization_status"]
    if (not hours["attempts"] or not holiday["attempts"]) and (
        hours["attempts"]
        or holiday["attempts"]
        or hours["status"] != "NOT_REQUESTED"
        or holiday["status"] != "NOT_REQUESTED"
    ):
        raise ValueError("incomplete source attempt sequence")
    if conversion == "NOT_ATTEMPTED":
        if (
            holiday["status"] == "CANDIDATE"
            or merge != "NOT_ATTEMPTED"
            or normalized != "NOT_ATTEMPTED"
        ):
            raise ValueError("unattempted conversion contradicts source or later steps")
    elif conversion != "CANDIDATE":
        if (
            holiday["status"] != "CANDIDATE"
            or merge != "NOT_ATTEMPTED"
            or normalized != "NOT_ATTEMPTED"
        ):
            raise ValueError("failed conversion contradicts source or later steps")
    else:
        if holiday["status"] != "CANDIDATE":
            raise ValueError("conversion lacks successful source")
        if merge == "PARTIAL":
            if normalized != "NORMALIZED":
                raise ValueError("partial merge lacks completed normalization")
        elif (
            merge not in {"MERGE_ERROR", "MERGE_UNAVAILABLE", "MERGE_PROTOCOL_ERROR"}
            or normalized != "NOT_ATTEMPTED"
        ):
            raise ValueError("conversion contradicts merge or normalization")


def _payload(merged, supplement, hours_page, source_checks):
    from bt_api_ctp.calendar_candidate_merge import _REQUIRED_REVIEW, _copy_validated
    from bt_api_ctp.calendar_hours_merge import _partial

    if (merged is None) == (supplement is None):
        raise ValueError("exactly one candidate input is required")
    candidate = _copy_validated(supplement) if merged is None else _partial(merged)
    nights = (
        None
        if merged is None
        else [
            {
                "holiday": n.holiday,
                "local_date": n.local_date.isoformat(),
                "source_id": n.source_id,
                "raw_text": n.raw_text,
                "trading_day": None,
            }
            for n in merged.unresolved_nights
        ]
    )
    try:
        hours = _page_to_data(hours_page)
    except (ValueError, TypeError, AttributeError):
        hours = None
    return {
        "content": candidate.content,
        "unresolved_nights": nights,
        "holiday_status": "UNKNOWN" if merged is None else "CAPTURED_UNVERIFIED",
        "unresolved_nights_status": "UNKNOWN" if merged is None else "CAPTURED_UNVERIFIED",
        "input_scope": "YAML_ONLY" if merged is None else "YAML_SHFE_PARTIAL",
        "source_checks": source_checks,
        "observed_at_utc": datetime.now(timezone.utc)  # noqa: UP017 - Python 3.9
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "clock_status": "CLOCK_UNVERIFIED",
        "remaining_checks": list(_REQUIRED_REVIEW if merged is None else merged.remaining_checks),
        "hours_page": hours,
        "hours_page_status": "CAPTURED_UNVERIFIED" if hours is not None else "UNKNOWN",
        "identity_mapping": "UNKNOWN",
        "night_mapping": "UNKNOWN",
        "baseline": "NOT_OBSERVED",
        "difference": "NOT_ASSESSED",
        "completeness": "INCOMPLETE",
        "verification": "UNVERIFIED",
        "publication": "NOT_AUTHORIZED",
        "durability": "UNCONFIRMED",
    }


def _validate_payload(payload):
    from bt_api_ctp.calendar_candidate_merge import PartialMergeResult
    from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate
    from bt_api_ctp.calendar_hours_merge import _partial
    from bt_api_ctp.calendar_shfe_candidates import UnresolvedNight
    from bt_api_ctp.calendar_supplement import _validate

    if type(payload) is not dict or set(payload) != {
        "content",
        "unresolved_nights",
        "holiday_status",
        "unresolved_nights_status",
        "input_scope",
        "source_checks",
        "observed_at_utc",
        "clock_status",
        "remaining_checks",
        "hours_page",
        "hours_page_status",
        "identity_mapping",
        "night_mapping",
        "baseline",
        "difference",
        "completeness",
        "verification",
        "publication",
        "durability",
    }:
        raise ValueError("invalid payload")
    if (
        payload["clock_status"] != "CLOCK_UNVERIFIED"
        or type(payload["observed_at_utc"]) is not str
        or not payload["observed_at_utc"].endswith("Z")
    ):
        raise ValueError("invalid observation clock")
    observed = datetime.fromisoformat(payload["observed_at_utc"][:-1] + "+00:00")
    if (
        observed.utcoffset() != timedelta(0)
        or observed.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != payload["observed_at_utc"]
    ):
        raise ValueError("invalid observation time")
    _validate_source_checks(payload["source_checks"])
    if tuple(
        payload[key]
        for key in (
            "identity_mapping",
            "night_mapping",
            "baseline",
            "difference",
            "completeness",
            "verification",
            "publication",
            "durability",
        )
    ) != (
        "UNKNOWN",
        "UNKNOWN",
        "NOT_OBSERVED",
        "NOT_ASSESSED",
        "INCOMPLETE",
        "UNVERIFIED",
        "NOT_AUTHORIZED",
        "UNCONFIRMED",
    ):
        raise ValueError("invalid qualification")
    if type(payload["remaining_checks"]) is not list:
        raise ValueError("invalid reviews")
    if payload["input_scope"] == "YAML_ONLY":
        if (
            payload["holiday_status"],
            payload["unresolved_nights_status"],
            payload["unresolved_nights"],
        ) != ("UNKNOWN", "UNKNOWN", None):
            raise ValueError("unknown holiday review must not be presented as empty")
        raw_nights = []
        if payload["source_checks"]["merge_status"] not in {
            "NOT_ATTEMPTED",
            "MERGE_ERROR",
            "MERGE_UNAVAILABLE",
            "MERGE_PROTOCOL_ERROR",
        }:
            raise ValueError("invalid YAML-only merge state")
    elif payload["input_scope"] == "YAML_SHFE_PARTIAL":
        if (
            (payload["holiday_status"], payload["unresolved_nights_status"])
            != ("CAPTURED_UNVERIFIED", "CAPTURED_UNVERIFIED")
            or type(payload["unresolved_nights"]) is not list
            or not payload["unresolved_nights"]
        ):
            raise ValueError("invalid holiday review")
        raw_nights = payload["unresolved_nights"]
        if (
            payload["source_checks"]["holiday"]["status"],
            payload["source_checks"]["holiday_conversion_status"],
            payload["source_checks"]["merge_status"],
            payload["source_checks"]["normalization_status"],
        ) != ("CANDIDATE", "CANDIDATE", "PARTIAL", "NORMALIZED"):
            raise ValueError("invalid merged source state")
    else:
        raise ValueError("invalid input scope")
    nights = []
    for item in raw_nights:
        if (
            type(item) is not dict
            or set(item) != {"holiday", "local_date", "source_id", "raw_text", "trading_day"}
            or item["trading_day"] is not None
        ):
            raise ValueError("invalid night")
        parsed = date.fromisoformat(item["local_date"])
        if parsed.isoformat() != item["local_date"]:
            raise ValueError("invalid date")
        nights.append(UnresolvedNight(item["holiday"], parsed, item["source_id"], item["raw_text"]))
    candidate = _partial(
        PartialMergeResult(
            "PARTIAL",
            _validate(payload["content"]),
            tuple(nights),
            remaining_checks=tuple(payload["remaining_checks"]),
        )
    )
    if normalize_calendar_candidate(candidate).status != "NORMALIZED":
        raise ValueError("ambiguous candidate")
    page = _page_from_data(payload["hours_page"])
    if payload["hours_page_status"] != ("CAPTURED_UNVERIFIED" if page is not None else "UNKNOWN"):
        raise ValueError("invalid hours status")
    if page is not None:
        hours = payload["source_checks"]["hours"]
        attempts = hours["attempts"]
        if not attempts:
            raise ValueError("captured page lacks source attempt")
        last = attempts[-1]
        if (
            last["transport_status"] != "HTTP_OK"
            or hours["status"] != page.status
            or last["source_status"] != page.status
            or last["parse_status"] != page.status
            or last["product_count"] != len(page.products)
        ):
            raise ValueError("captured page disagrees with source attempt")


def _open_inbox(root, *, create):
    descriptors, chain = _open_directory(root)
    root_fd = descriptors[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("inbox", 0o700, dir_fd=root_fd)
        info = os.stat("inbox", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("inbox is not a directory")
        fd = os.open("inbox", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        descriptors.append(fd)
        identity = _identity(os.fstat(fd))
        if _identity(info) != identity:
            raise ValueError("inbox changed")
        chain.append((root_fd, "inbox", fd, identity))
        return descriptors, chain
    except Exception:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)
        raise


def retain_candidate(
    *, root, candidate_id, merged, hours_page, source_checks, filesystem, supplement=None
):
    if type(candidate_id) is not str or _ID.fullmatch(candidate_id) is None:
        return CandidateRetention("NOT_COMMITTED", "INVALID_ID", "")
    if filesystem != TRUSTED_LOCAL_POSIX or not _supported():
        return CandidateRetention("NOT_COMMITTED", "UNSUPPORTED_FILESYSTEM", candidate_id)
    try:
        payload = _payload(merged, supplement, hours_page, source_checks)
        # Validate the detached representation actually written, not caller objects.
        payload = json.loads(_canonical(payload))
        _validate_payload(payload)
        body = _canonical(payload)
        envelope = _canonical(
            {
                "schema": _SCHEMA,
                "candidate_id": candidate_id,
                "payload": payload,
                "payload_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        if len(envelope) > _LIMIT:
            raise ValueError("candidate too large")
    except Exception:
        return CandidateRetention("NOT_COMMITTED", "INVALID_OR_TOO_LARGE", candidate_id)
    try:
        descriptors, chain = _open_inbox(root, create=True)
    except Exception:
        return CandidateRetention("NOT_COMMITTED", "INVALID_DIRECTORY", candidate_id)
    directory = descriptors[-1]
    temp = ""
    name = f"candidate-{candidate_id}.json"
    temp_fd, temp_identity, attempted = None, None, False
    try:
        temp = f".candidate-{candidate_id}-{uuid.uuid4().hex}.tmp"
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        temp_fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        temp_identity = _identity(os.fstat(temp_fd))
        os.fchmod(temp_fd, 0o600)
        offset = 0
        while offset < len(envelope):
            count = os.write(temp_fd, envelope[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fsync(temp_fd)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        attempted = True
        try:
            os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if _identity(existing) == temp_identity or not _directory_matches(chain):
                raise
            attempted = False
            return CandidateRetention("NOT_COMMITTED", "ID_EXISTS", candidate_id)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            if _identity(os.fstat(fd)) != temp_identity:
                raise ValueError("candidate changed")
            data = bytearray()
            while len(data) <= _LIMIT:
                part = os.read(fd, _LIMIT + 1 - len(data))
                if not part:
                    break
                data.extend(part)
            if bytes(data) != envelope or not _directory_matches(chain):
                raise ValueError("candidate readback mismatch")
        finally:
            os.close(fd)
        os.unlink(temp, dir_fd=directory)
        temp_identity = None
        os.fsync(directory)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return CandidateRetention("COMMITTED", "NONE", candidate_id)
    except Exception:
        return CandidateRetention(
            "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED", "IO_ERROR", candidate_id
        )
    finally:
        if temp_fd is not None:
            with contextlib.suppress(OSError):
                os.close(temp_fd)
        if temp_identity is not None:
            _remove_owned_temp(directory, temp, temp_identity)
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)


def read_candidate(*, root, candidate_id, filesystem):
    """Return candidate data after structural/digest checks; never authenticate it."""
    if (
        filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or type(candidate_id) is not str
        or _ID.fullmatch(candidate_id) is None
    ):
        raise ValueError("candidate unavailable")
    descriptors, chain = _open_inbox(root, create=False)
    try:
        fd = os.open(
            f"candidate-{candidate_id}.json",
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=descriptors[-1],
        )
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size > _LIMIT
                or before.st_mode & 0o077
            ):
                raise ValueError("candidate invalid")
            data = bytearray()
            while len(data) <= _LIMIT:
                part = os.read(fd, _LIMIT + 1 - len(data))
                if not part:
                    break
                data.extend(part)
            after = os.fstat(fd)
            final_entry = os.stat(
                f"candidate-{candidate_id}.json", dir_fd=descriptors[-1], follow_symlinks=False
            )
            if (
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(data) != before.st_size
                or _identity(final_entry) != _identity(after)
                or not _directory_matches(chain)
            ):
                raise ValueError("candidate changed")
        finally:
            os.close(fd)

        def pairs(rows):
            result = {}
            for key, value in rows:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        envelope = json.loads(data, object_pairs_hook=pairs)
        if (
            type(envelope) is not dict
            or set(envelope) != {"schema", "candidate_id", "payload", "payload_sha256"}
            or envelope["schema"] != _SCHEMA
            or envelope["candidate_id"] != candidate_id
            or type(envelope["payload_sha256"]) is not str
            or _SHA.fullmatch(envelope["payload_sha256"]) is None
            or hashlib.sha256(_canonical(envelope["payload"])).hexdigest()
            != envelope["payload_sha256"]
        ):
            raise ValueError("candidate digest mismatch")
        _validate_payload(envelope["payload"])
        return envelope
    finally:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("show", choices=["show"])
    parser.add_argument("--calendar-root", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--trusted-local-posix", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        item = read_candidate(
            root=args.calendar_root, candidate_id=args.candidate_id, filesystem=TRUSTED_LOCAL_POSIX
        )
    except Exception:
        print(
            json.dumps(
                {
                    "status": "UNAVAILABLE",
                    "candidate_id": args.candidate_id if _ID.fullmatch(args.candidate_id) else "",
                }
            )
        )
        return 20
    print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
