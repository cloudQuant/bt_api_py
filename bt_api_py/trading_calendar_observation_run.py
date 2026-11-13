"""Explicit single preview observation and local per-venue run records.

This is not an updater, scheduler, approval, publication, account access or
email sender. ``run`` outputs its random ID before attempting any write; the
intent and result are separately installed without overwrite under the explicit
root. An orphan intent is running-or-incomplete, not proof of either outcome.
``show`` checks local structure and hashes only: they authenticate neither the
writer nor source data, and cannot prove power-loss durability. A filesystem
and directory-chain check cannot exclude ABA, hostile writers or native races.

The existing preview supervisor's budget excludes local record I/O, fsync and
process creation. Network/process cleanup, total CLI time, wall-clock ordering
and cross-process chronology are not proven. Explicit detail collection retries
only eligible timeouts within its separate budget.
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
import time
import uuid
from dataclasses import replace
from datetime import date

from bt_api_py.trading_calendar_preview import Attempt, PreviewReport, _supervise, _valid_number
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _remove_owned_temp,
    _supported,
    _validated_report,
)

_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_INTENT_SCHEMA = "bt_api.calendar-observation-intent/1"
_RESULT_SCHEMA = "bt_api.calendar-observation-run/1"
_INTENT_SCHEMA_V2 = "bt_api.calendar-observation-intent/2"
_RESULT_SCHEMA_V2 = "bt_api.calendar-observation-run/2"
_INTENT_SCHEMA_V3 = "bt_api.calendar-observation-intent/3"
_RESULT_SCHEMA_V3 = "bt_api.calendar-observation-run/3"
_INTENT_SCHEMA_V4 = "bt_api.calendar-observation-intent/4"
_RESULT_SCHEMA_V4 = "bt_api.calendar-observation-run/4"
_INTENT_SCHEMA_V5 = "bt_api.calendar-observation-intent/5"
_RESULT_SCHEMA_V5 = "bt_api.calendar-observation-run/5"
_INTENT_SCHEMA_V6 = "bt_api.calendar-observation-intent/6"
_RESULT_SCHEMA_V6 = "bt_api.calendar-observation-run/6"
_LIMIT = 65536
_LIST_ENTRY_LIMIT = 200
_LIST_ENTRY_MAX = 2000
_VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
_INE_RETRY_UNCOVERED = ["HTTP_403", "HTTP_429", "HTTP_5XX", "HTTP_ERROR", "NETWORK_ERROR"]


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _open_runs(root, *, create):
    descriptors, chain = _open_directory(root)
    parent = descriptors[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("observation-runs", 0o700, dir_fd=parent)
        info = os.stat("observation-runs", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("invalid run directory")
        fd = os.open(
            "observation-runs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        descriptors.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "observation-runs", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return descriptors, chain
    except Exception:
        for fd in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(fd)
        raise


def _close(descriptors):
    for fd in reversed(descriptors):
        with contextlib.suppress(OSError):
            os.close(fd)


def _read_entry(directory, chain, name):
    if not _directory_matches(chain):
        raise ValueError("directory changed")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        if not _directory_matches(chain):
            raise ValueError("directory changed") from None
        return None
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_mode & 0o077
            or before.st_uid != os.geteuid()
            or before.st_size > _LIMIT
        ):
            raise ValueError("invalid file")
        data = bytearray()
        while len(data) <= _LIMIT:
            part = os.read(fd, _LIMIT + 1 - len(data))
            if not part:
                break
            data.extend(part)
        after = os.fstat(fd)
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)

        def identity(x):
            return (
                x.st_dev,
                x.st_ino,
                x.st_mode,
                x.st_uid,
                x.st_size,
                x.st_mtime_ns,
                x.st_ctime_ns,
            )

        if (
            len(data) != before.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(entry)
            or not _directory_matches(chain)
        ):
            raise ValueError("file changed")
        return bytes(data)
    finally:
        os.close(fd)


def _write_once(directory, chain, name, data):
    """Return visibility as local evidence only; never infer fsync durability."""
    if type(data) is not bytes or len(data) > _LIMIT or not _directory_matches(chain):
        return "NOT_COMMITTED", "INVALID_INPUT"
    temp = ""
    fd, ident, attempted = None, None, False
    try:
        temp = f".observation-{uuid.uuid4().hex}.tmp"
        fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        os.fchmod(fd, 0o600)
        ident = _identity(os.fstat(fd))
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fsync(fd)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        attempted = True
        try:
            os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            old = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if _identity(old) == ident or not _directory_matches(chain):
                raise
            attempted = False
            return "NOT_COMMITTED", "ID_EXISTS"
        if _read_entry(directory, chain, name) != data:
            raise ValueError("readback mismatch")
        os.unlink(temp, dir_fd=directory)
        ident = None
        os.fsync(directory)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return "COMMITTED", "NONE"
    except Exception:
        return ("COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED"), "IO_ERROR"
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if ident is not None:
            _remove_owned_temp(directory, temp, ident)


def _valid_ine_retry_config(config, run_id, candidate_id, first_ids):
    if (
        type(config) is not dict
        or set(config) != {"evidence_ids", "budget_seconds", "interval_seconds"}
        or type(config["evidence_ids"]) is not dict
        or set(config["evidence_ids"]) != {"hours", "holiday"}
        or not _valid_number(config["budget_seconds"], 0.1, 86400.0)
        or not _valid_number(config["interval_seconds"], 0.0, 86400.0)
    ):
        return False
    ids = config["evidence_ids"]
    if (
        any(type(v) is not list or not 1 <= len(v) <= 3 for v in ids.values())
        or len(ids["hours"]) != len(ids["holiday"])
        or any(not _id(eid) for values in ids.values() for eid in values)
        or first_ids != {kind: values[0] for kind, values in ids.items()}
    ):
        return False
    all_ids = [eid for values in ids.values() for eid in values]
    return len(set(all_ids + [run_id, candidate_id])) == len(all_ids) + 2


def _intent(
    run_id,
    enable_shfe,
    candidate_id="",
    fetch_product_navigation=False,
    ine_ids=None,
    ine_retry_config=None,
    capture_id="",
):
    body = {
        "schema": _INTENT_SCHEMA,
        "run_id": run_id,
        "shfe_requested": enable_shfe,
        "verification": "UNVERIFIED",
        "writer_identity": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
    }
    if candidate_id:
        body.update({"schema": _INTENT_SCHEMA_V2, "candidate_id": candidate_id})
    if fetch_product_navigation:
        body.update({"schema": _INTENT_SCHEMA_V3, "navigation_requested": True})
    if ine_ids is not None:
        body.update(
            schema=_INTENT_SCHEMA_V4,
            candidate_id=candidate_id,
            navigation_requested=fetch_product_navigation,
            ine_evidence_ids=ine_ids,
        )
    if ine_retry_config is not None:
        body.update(schema=_INTENT_SCHEMA_V5, ine_retry_config=ine_retry_config)
    if capture_id:
        body.update(base_schema=body["schema"], schema=_INTENT_SCHEMA_V6, capture_id=capture_id)
    return _canonical({"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()})


def _valid_intent(raw, run_id):
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(value) is not dict or set(value) != {"body", "sha256"}:
        raise ValueError("invalid intent")
    body = value["body"]
    if type(body) is dict and body.get("schema") == _INTENT_SCHEMA_V6:
        base = dict(body)
        base_schema = base.pop("base_schema", None)
        capture_id = base.pop("capture_id", None)
        base["schema"] = base_schema
        if (
            base_schema
            not in {
                _INTENT_SCHEMA,
                _INTENT_SCHEMA_V2,
                _INTENT_SCHEMA_V3,
                _INTENT_SCHEMA_V4,
                _INTENT_SCHEMA_V5,
            }
            or not _id(capture_id)
            or capture_id == run_id
            or capture_id == base.get("candidate_id")
            or capture_id in (base.get("ine_evidence_ids") or {}).values()
            or type(value["sha256"]) is not str
            or _SHA.fullmatch(value["sha256"]) is None
            or hashlib.sha256(_canonical(body)).hexdigest() != value["sha256"]
        ):
            raise ValueError("invalid capture intent")
        _valid_intent(
            _canonical({"body": base, "sha256": hashlib.sha256(_canonical(base)).hexdigest()}),
            run_id,
        )
        retry = base.get("ine_retry_config")
        if retry is not None and any(capture_id in ids for ids in retry["evidence_ids"].values()):
            raise ValueError("capture ID conflict")
        return body
    if (
        type(body) is not dict
        or body.get("schema")
        not in {
            _INTENT_SCHEMA,
            _INTENT_SCHEMA_V2,
            _INTENT_SCHEMA_V3,
            _INTENT_SCHEMA_V4,
            _INTENT_SCHEMA_V5,
        }
        or set(body)
        != {"schema", "run_id", "shfe_requested", "verification", "writer_identity", "durability"}
        | (
            {"candidate_id"}
            if body.get("schema")
            in {_INTENT_SCHEMA_V2, _INTENT_SCHEMA_V3, _INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}
            else set()
        )
        | (
            {"navigation_requested"}
            if body.get("schema") in {_INTENT_SCHEMA_V3, _INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}
            else set()
        )
        | (
            {"ine_evidence_ids"}
            if body.get("schema") in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}
            else set()
        )
        | ({"ine_retry_config"} if body.get("schema") == _INTENT_SCHEMA_V5 else set())
        or (
            body.get("schema") in {_INTENT_SCHEMA_V2, _INTENT_SCHEMA_V3}
            and not _id(body.get("candidate_id"))
        )
        or (
            body.get("schema") in {_INTENT_SCHEMA_V2, _INTENT_SCHEMA_V3}
            and body.get("candidate_id") == run_id
        )
        or (
            body.get("schema") == _INTENT_SCHEMA_V3 and body.get("navigation_requested") is not True
        )
        or (
            body.get("schema") in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}
            and (
                type(body.get("navigation_requested")) is not bool
                or type(body.get("candidate_id")) is not str
                or (body["candidate_id"] and not _id(body["candidate_id"]))
                or (body["navigation_requested"] and not body["candidate_id"])
                or type(body.get("ine_evidence_ids")) is not dict
                or set(body["ine_evidence_ids"]) != {"hours", "holiday"}
                or any(not _id(v) for v in body["ine_evidence_ids"].values())
                or len({run_id, body["candidate_id"], *body["ine_evidence_ids"].values()}) != 4
            )
        )
        or (
            body.get("schema") == _INTENT_SCHEMA_V5
            and not _valid_ine_retry_config(
                body.get("ine_retry_config"),
                run_id,
                body.get("candidate_id"),
                body.get("ine_evidence_ids"),
            )
        )
        or body["run_id"] != run_id
        or type(body["shfe_requested"]) is not bool
        or (body["verification"], body["writer_identity"], body["durability"])
        != ("UNVERIFIED", "NOT_PROVEN", "NOT_PROVEN")
        or type(value["sha256"]) is not str
        or _SHA.fullmatch(value["sha256"]) is None
        or hashlib.sha256(_canonical(body)).hexdigest() != value["sha256"]
    ):
        raise ValueError("invalid intent")
    return body


def _report_data(report, candidate_id=""):
    raw = _validated_report(report)
    if raw is None:
        return None
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    # This run does not create candidate files; any such claim is unsupported.
    if candidate_id:
        if value["candidate_id"] != candidate_id or value["candidate_status"] == "NOT_REQUESTED":
            return None
    elif (
        value["candidate_id"]
        or value["candidate_status"] != "NOT_REQUESTED"
        or value["candidate_reason"] != "NONE"
    ):
        return None
    return value


def _rehydrate(value, candidate_id=""):
    if type(value) is not dict:
        return None
    try:
        data = dict(value)
        data["attempts"] = tuple(Attempt(**row) for row in data["attempts"])
        data["holiday_attempts"] = tuple(Attempt(**row) for row in data["holiday_attempts"])
        report = PreviewReport(**data)
        checked = _report_data(report, candidate_id)
        return checked if checked == value else None
    except Exception:
        return None


_UNSTARTED = frozenset({"SUPERVISOR_UNSUPPORTED", "WORKER_START_FAILED"})
_UNCERTAIN = frozenset(
    {
        "BUDGET_EXHAUSTED",
        "WORKER_CLEANUP_UNCERTAIN",
        "WORKER_COMMUNICATION_ERROR",
        "WORKER_PROTOCOL_ERROR",
        "CHILD_CLEANUP_UNCERTAIN",
    }
)


def _candidate_state(data):
    if data is None:
        return "COMMIT_UNKNOWN"
    status, reason = data["candidate_status"], data["candidate_reason"]
    if data["reason"] in _UNCERTAIN:
        return "COMMIT_UNKNOWN"
    if data["reason"] in _UNSTARTED:
        if status == "NOT_ATTEMPTED" and reason == "NONE":
            return "NOT_ATTEMPTED"
        return "COMMIT_UNKNOWN"
    if status == "COMMITTED" and reason == "NONE":
        return "COMMITTED"
    if status == "NOT_COMMITTED" and reason in {
        "INVALID_ID",
        "UNSUPPORTED_FILESYSTEM",
        "INVALID_OR_TOO_LARGE",
        "INVALID_DIRECTORY",
        "ID_EXISTS",
        "IO_ERROR",
    }:
        return "NOT_COMMITTED"
    if status == "COMMIT_UNKNOWN" and reason in {"IO_ERROR", "CHILD_CLEANUP_UNCERTAIN"}:
        return "COMMIT_UNKNOWN"
    return "COMMIT_UNKNOWN"


def _candidate_report(report, candidate_id):
    """Project ambiguous supervisor outcomes without upgrading candidate state."""
    raw = _validated_report(report)
    if raw is None:
        return None
    data = json.loads(raw, object_pairs_hook=_unique_pairs)
    if data["reason"] in _UNSTARTED and (
        data["candidate_id"],
        data["candidate_status"],
        data["candidate_reason"],
    ) == ("", "NOT_REQUESTED", "NONE"):
        report = replace(
            report,
            candidate_id=candidate_id,
            candidate_status="NOT_ATTEMPTED",
            candidate_reason="NONE",
        )
    elif (
        data["reason"] in _UNSTARTED
        or data["candidate_id"] != candidate_id
        or data["reason"] in _UNCERTAIN
        or data["candidate_status"] in {"IN_PROGRESS", "NOT_REQUESTED", "NOT_ATTEMPTED"}
        or _candidate_state(data) == "COMMIT_UNKNOWN"
        and data["candidate_status"] != "COMMIT_UNKNOWN"
    ):
        report = replace(
            report,
            candidate_id=candidate_id,
            candidate_status="COMMIT_UNKNOWN",
            candidate_reason="IO_ERROR",
        )
    data = _report_data(report, candidate_id)
    if data is None or (
        data["candidate_status"] == "COMMIT_UNKNOWN"
        and data["candidate_reason"] not in {"IO_ERROR", "CHILD_CLEANUP_UNCERTAIN"}
    ):
        return None
    return data


def _source(report, attribute, requested):
    if report is None:
        return {
            "state": "UNKNOWN" if requested else "NOT_ATTEMPTED",
            "transport": "UNKNOWN" if requested else "NOT_ATTEMPTED",
            "parse": "UNKNOWN" if requested else "NOT_ATTEMPTED",
            "verification": "UNVERIFIED",
        }
    attempts = report["attempts"] if attribute == "hours" else report["holiday_attempts"]
    if not attempts:
        known_unstarted = not requested or report["reason"] in {
            "SUPERVISOR_UNSUPPORTED",
            "WORKER_START_FAILED",
            "INVALID_ARGUMENTS",
            "INPUT_CHECK_FAILED",
            "SHFE_NOT_REQUESTED",
        }
        return {
            "state": "NOT_ATTEMPTED" if known_unstarted else "UNKNOWN",
            "transport": "NOT_ATTEMPTED" if known_unstarted else "UNKNOWN",
            "parse": "NOT_ATTEMPTED" if known_unstarted else "UNKNOWN",
            "verification": "UNVERIFIED",
        }
    last = attempts[-1]
    transport, parse = last["transport_status"], last["parse_status"]
    if transport == "SUPERVISOR_UNSUPPORTED":
        # This collector invocation did not start; earlier retries may have.
        state = "NOT_ATTEMPTED" if len(attempts) == 1 else "UNKNOWN"
    elif transport == "HTTP_OK" and parse in {"CANDIDATE", "NEEDS_REVIEW"}:
        state = "STRUCTURAL_CANDIDATE" if parse == "CANDIDATE" else "NEEDS_REVIEW"
    elif (
        transport
        in {
            "IN_PROGRESS",
            "INTERRUPTED",
            "CHILD_TIMEOUT",
            "CHILD_CLEANUP_UNCERTAIN",
            "CHILD_ERROR",
            "COLLECTOR_ERROR",
            "COLLECTOR_PROTOCOL_ERROR",
            "CTP_UNAVAILABLE",
        }
        or transport == "HTTP_OK"
    ):
        state = "UNKNOWN"
    else:
        state = "FAILED"
    return {"state": state, "transport": transport, "parse": parse, "verification": "UNVERIFIED"}


def _venues(report, requested):
    hours, holiday = _source(report, "hours", requested), _source(report, "holiday", requested)
    states = {hours["state"], holiday["state"]}
    if states == {"NOT_ATTEMPTED"}:
        state = "NOT_ATTEMPTED"
    elif "UNKNOWN" in states:
        state = "UNKNOWN"
    elif "FAILED" in states:
        state = "FAILED"
    else:
        state = "PARTIAL_OBSERVATION"
    rows = [
        {
            "venue": "SHFE",
            "state": state,
            "reason": "UNVERIFIED_PREVIEW",
            "hours": hours,
            "holiday": holiday,
        }
    ]
    rows.extend(
        {
            "venue": venue,
            "state": "NOT_ATTEMPTED",
            "reason": "NOT_IMPLEMENTED",
            "hours": None,
            "holiday": None,
        }
        for venue in _VENUES[1:]
    )
    return rows


_INE_TRANSPORT = frozenset(
    {
        "HTTP_OK",
        "HTTP_ERROR",
        "REDIRECT",
        "TLS_ERROR",
        "NETWORK_TIMEOUT",
        "NETWORK_ERROR",
        "RESPONSE_TOO_LARGE",
        "EMPTY_RESPONSE",
        "CHILD_ERROR",
        "CHILD_TIMEOUT",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
    }
)
_INE_PARSE = (
    frozenset(
        {
            "CANDIDATE",
            "NEEDS_REVIEW",
            "ACCESS_CHALLENGE",
            "INVALID_INPUT",
            "INVALID_SOURCE",
            "INVALID_UTF8",
            "INPUT_TOO_LARGE",
            "STRUCTURE_CHANGED",
            "PARSER_ERROR",
        }
    )
    | _INE_TRANSPORT
)


def _ine_unstarted(evidence_id):
    return {
        "evidence_id": evidence_id,
        "state": "NOT_ATTEMPTED",
        "capture": "NOT_ATTEMPTED",
        "claim": "NOT_ATTEMPTED",
        "result_commit": "NOT_ATTEMPTED",
        "transport": "NOT_ATTEMPTED",
        "parse": "NOT_ATTEMPTED",
        "payload_sha256": None,
        "claim_sha256": None,
    }


def _ine_classify(capture, transport, parse, digest, claim_digest):
    if capture == "NOT_ATTEMPTED":
        return "NOT_ATTEMPTED"
    if (
        capture != "COMMITTED"
        or transport not in _INE_TRANSPORT | {"NOT_STARTED_BUDGET_EXHAUSTED"}
        or parse not in _INE_PARSE | {"NOT_STARTED_BUDGET_EXHAUSTED"}
        or not isinstance(digest, str)
        or not _SHA.fullmatch(digest)
        or type(claim_digest) is not str
        or not _SHA.fullmatch(claim_digest)
    ):
        return "UNKNOWN"
    if transport == parse == "NOT_STARTED_BUDGET_EXHAUSTED":
        return "NOT_STARTED_BUDGET_EXHAUSTED"
    if "NOT_STARTED_BUDGET_EXHAUSTED" in {transport, parse}:
        return "UNKNOWN"
    if transport in {
        "CHILD_ERROR",
        "CHILD_TIMEOUT",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
    }:
        return "UNKNOWN"
    if transport != "HTTP_OK" or parse == "ACCESS_CHALLENGE":
        return "FAILED"
    if parse in {"CANDIDATE", "NEEDS_REVIEW"}:
        return "PARTIAL_OBSERVATION"
    return "UNKNOWN"


def _preview_cleanup_uncertain(preview):
    return (
        preview is None
        or preview["reason"] in _UNCERTAIN
        or preview["candidate_reason"] == "CHILD_CLEANUP_UNCERTAIN"
        or preview["candidate_status"] == "COMMIT_UNKNOWN"
        or any(
            item["transport_status"] in {"CHILD_CLEANUP_UNCERTAIN", "CHILD_ERROR"}
            for item in preview["attempts"] + preview["holiday_attempts"]
        )
    )


def _ine_claim_digest(root, filesystem, evidence_id, kind, expected=None):
    """Read the local claim by ID; its digest is not source or writer authentication."""
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_source_evidence as evidence

    if not evidence._valid(root, evidence_id, filesystem):
        raise ValueError("invalid evidence")
    descriptors, chain = evidence._open(root, False)
    try:
        raw = storage._read(descriptors[-1], chain, f"intent-{evidence_id}.json", evidence._LIMIT)
        if raw is None:
            raise ValueError("missing claim")
        claim, digest = storage._unwrap(raw)
        if (
            set(claim)
            != {
                "schema",
                "evidence_id",
                "exchange",
                "kind",
                "connect_timeout",
                "read_timeout",
                "total_timeout",
                "max_bytes",
            }
            or claim["schema"] != evidence._INTENT_INE
            or claim["evidence_id"] != evidence_id
            or claim["exchange"] != "INE"
            or claim["kind"] != kind
            or (
                expected is not None and any(claim[key] != value for key, value in expected.items())
            )
            or not _directory_matches(chain)
        ):
            raise ValueError("claim mismatch")
        evidence._settings(
            kind,
            claim["connect_timeout"],
            claim["read_timeout"],
            claim["total_timeout"],
            claim["max_bytes"],
        )
        return digest
    finally:
        _close(descriptors)


def _ine_observations(root, filesystem, ids, deadline, *, _only=None, _deadline_capture=False):
    from bt_api_py import trading_calendar_source_evidence as evidence

    rows = {kind: _ine_unstarted(eid) for kind, eid in ids.items()}
    for kind in ("hours", "holiday") if _only is None else (_only,):
        remaining = deadline - time.monotonic()
        if remaining < 0.1:
            break
        row = rows[kind]
        row.update(
            state="UNKNOWN",
            capture="ATTEMPT_UNKNOWN",
            claim="UNKNOWN",
            result_commit="UNKNOWN",
            transport="UNKNOWN",
            parse="UNKNOWN",
        )
        try:
            settings = {
                "connect_timeout": min(5.0, remaining),
                "read_timeout": min(20.0, remaining),
                "total_timeout": min(60.0, remaining),
                "max_bytes": 1024 * 1024,
            }
            result = evidence.capture(
                root=root,
                evidence_id=row["evidence_id"],
                kind=kind,
                exchange="INE",
                filesystem=filesystem,
                emit=lambda _: None,
                **({"monotonic_deadline": deadline} if _deadline_capture else {}),
                **settings,
            )
            capture = result.get("status") if type(result) is dict else None
            if capture not in {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN", "ATTEMPT_UNKNOWN"}:
                break
            row["capture"] = capture
            if capture == "COMMITTED":
                row.update(claim="COMMITTED", result_commit="COMMITTED")
            elif (
                result.get("reason")
                in {
                    "CHILD_OR_RESULT_UNCERTAIN",
                    "RESULT_DIRECTORY_UNAVAILABLE",
                    "RESULT_NOT_COMMITTED",
                }
                or (
                    result.get("reason") == "CLAIM_CLEANUP_UNCERTAIN"
                    and capture == "ATTEMPT_UNKNOWN"
                )
                or "transport" in result
            ):
                row["claim"] = "COMMITTED"
            if capture != "COMMITTED":
                break
            observed = evidence.show(
                root=root, evidence_id=row["evidence_id"], filesystem=filesystem
            )
            if _deadline_capture and observed.get("status") == "NOT_STARTED_BUDGET_EXHAUSTED":
                if (
                    observed.get("exchange"),
                    observed.get("kind"),
                    observed.get("evidence_id"),
                ) != ("INE", kind, row["evidence_id"]):
                    break
                row.update(
                    transport="NOT_STARTED_BUDGET_EXHAUSTED",
                    parse="NOT_STARTED_BUDGET_EXHAUSTED",
                    payload_sha256=observed.get("payload_sha256"),
                    claim_sha256=_ine_claim_digest(
                        root, filesystem, row["evidence_id"], kind, settings
                    ),
                )
                row["state"] = _ine_classify(
                    capture,
                    row["transport"],
                    row["parse"],
                    row["payload_sha256"],
                    row["claim_sha256"],
                )
                break
            if (
                observed.get("status"),
                observed.get("exchange"),
                observed.get("kind"),
                observed.get("evidence_id"),
            ) != ("VISIBLE_UNAUTHENTICATED", "INE", kind, row["evidence_id"]):
                break
            if (
                observed.get("transport") not in _INE_TRANSPORT
                or observed.get("page_status") not in _INE_PARSE
                or type(observed.get("payload_sha256")) is not str
                or not _SHA.fullmatch(observed["payload_sha256"])
            ):
                break
            if _deadline_capture and type(observed.get("transport_settings")) is not dict:
                break
            claim_digest = _ine_claim_digest(root, filesystem, row["evidence_id"], kind, settings)
            row.update(
                transport=observed.get("transport"),
                parse=observed.get("page_status"),
                payload_sha256=observed.get("payload_sha256"),
                claim_sha256=claim_digest,
            )
            row["state"] = _ine_classify(
                capture, row["transport"], row["parse"], row["payload_sha256"], row["claim_sha256"]
            )
            if row["state"] == "UNKNOWN":
                break
        except BaseException:
            break
    return rows


def _ine_retry_observations(root, filesystem, ids, budget_seconds, retry_interval_seconds):
    """Collect reserved INE IDs in one process; the caller must persist *all* IDs first.

    This helper is not an entry point. It must only be wired into a versioned
    intent/result contract that persists the returned attempts and prevents
    resumption of incomplete runs. Deadlines exclude local file I/O and cleanup.
    """
    if (
        type(ids) is not dict
        or set(ids) != {"hours", "holiday"}
        or any(type(values) is not list or not 1 <= len(values) <= 3 for values in ids.values())
        or len(ids["hours"]) != len(ids["holiday"])
        or any(not _id(eid) for values in ids.values() for eid in values)
        or len({eid for values in ids.values() for eid in values})
        != sum(len(values) for values in ids.values())
        or not _valid_number(budget_seconds, 0.1, 86400.0)
        or not _valid_number(retry_interval_seconds, 0.0, 86400.0)
    ):
        raise ValueError("invalid INE retry settings")
    deadline = time.monotonic() + budget_seconds
    result = {
        "attempts": {kind: [] for kind in ("hours", "holiday")},
        "order": [],
        "stop_reason": "NONE",
        "uncovered_failures": _INE_RETRY_UNCOVERED[:],
    }
    for kind in ("hours", "holiday"):
        completed_at = 0.0
        for index, evidence_id in enumerate(ids[kind]):
            if deadline - time.monotonic() < 0.1:
                result["stop_reason"] = "RETRY_BUDGET_EXHAUSTED"
                return result
            if index:
                eligible_at = completed_at + retry_interval_seconds
                wait = max(0.0, eligible_at - time.monotonic())
                if deadline - time.monotonic() < wait + 0.1:
                    result["stop_reason"] = "RETRY_BUDGET_EXHAUSTED"
                    return result
                try:
                    if wait:
                        time.sleep(wait)
                except BaseException:
                    result["stop_reason"] = "WAIT_INTERRUPTED"
                    return result
                # Do not mistake an interrupted/early sleep or a reset clock for
                # the required wait having elapsed.
                now = time.monotonic()
                if now < eligible_at:
                    result["stop_reason"] = "WAIT_UNCERTAIN"
                    return result
                if deadline - now < 0.1:
                    result["stop_reason"] = "RETRY_BUDGET_EXHAUSTED"
                    return result
            try:
                prior_visible = all(
                    _ine_visibility(
                        root, filesystem, {"ine_observations": {previous_kind: previous_row}}
                    )[previous_kind]
                    == "VISIBLE_UNAUTHENTICATED"
                    for previous_kind, previous_rows in result["attempts"].items()
                    for previous_row in previous_rows
                )
            except BaseException:
                prior_visible = False
            if not prior_visible:
                result["stop_reason"] = "INE_EVIDENCE_CHANGED_OR_MISSING"
                return result
            if deadline - time.monotonic() < 0.1:
                result["stop_reason"] = "RETRY_BUDGET_EXHAUSTED"
                return result
            pair = {key: ids[key][0] for key in ("hours", "holiday")}
            pair[kind] = evidence_id
            row = _ine_observations(
                root, filesystem, pair, deadline, _only=kind, _deadline_capture=True
            )[kind]
            completed_at = time.monotonic()
            if row["state"] == "NOT_ATTEMPTED":
                result["stop_reason"] = "RETRY_BUDGET_EXHAUSTED"
                return result
            result["attempts"][kind].append(row)
            result["order"].append({"kind": kind, "evidence_id": evidence_id})
            if row["state"] == "NOT_STARTED_BUDGET_EXHAUSTED":
                result["stop_reason"] = "RETRY_BUDGET_EXHAUSTED"
                return result
            if row["state"] == "UNKNOWN":
                result["stop_reason"] = "INE_CLEANUP_OR_RESULT_UNCERTAIN"
                return result
            if not (
                row["capture"] == "COMMITTED"
                and row["state"] == "FAILED"
                and row["transport"] == "NETWORK_TIMEOUT"
                and row["parse"] == "NETWORK_TIMEOUT"
                and row["claim_sha256"]
                and row["payload_sha256"]
            ):
                break
    return result


def _valid_ine(rows, ids, *, allow_not_started=False):
    if type(rows) is not dict or set(rows) != {"hours", "holiday"}:
        return False
    for kind, row in rows.items():
        if (
            type(row) is not dict
            or set(row)
            != {
                "evidence_id",
                "state",
                "capture",
                "claim",
                "result_commit",
                "transport",
                "parse",
                "payload_sha256",
                "claim_sha256",
            }
            or row["evidence_id"] != ids[kind]
            or row["capture"]
            not in {
                "NOT_ATTEMPTED",
                "COMMITTED",
                "NOT_COMMITTED",
                "COMMIT_UNKNOWN",
                "ATTEMPT_UNKNOWN",
            }
            or row["state"]
            != _ine_classify(
                row["capture"],
                row["transport"],
                row["parse"],
                row["payload_sha256"],
                row["claim_sha256"],
            )
            or row["claim"] not in {"NOT_ATTEMPTED", "COMMITTED", "UNKNOWN"}
            or row["result_commit"] not in {"NOT_ATTEMPTED", "COMMITTED", "UNKNOWN"}
            or (
                row["capture"] == "COMMITTED"
                and (row["claim"], row["result_commit"]) != ("COMMITTED", "COMMITTED")
            )
            or (
                row["capture"] not in {"COMMITTED", "NOT_ATTEMPTED"}
                and (row["claim"] == "NOT_ATTEMPTED" or row["result_commit"] != "UNKNOWN")
            )
            or (row["capture"] == "NOT_ATTEMPTED" and row != _ine_unstarted(ids[kind]))
            or (
                row["capture"] not in {"COMMITTED", "NOT_ATTEMPTED"}
                and (row["transport"], row["parse"], row["payload_sha256"], row["claim_sha256"])
                != ("UNKNOWN", "UNKNOWN", None, None)
            )
            or (
                row["capture"] == "COMMITTED"
                and (
                    row["transport"]
                    not in _INE_TRANSPORT
                    | {"UNKNOWN"}
                    | ({"NOT_STARTED_BUDGET_EXHAUSTED"} if allow_not_started else set())
                    or row["parse"]
                    not in _INE_PARSE
                    | {"UNKNOWN"}
                    | ({"NOT_STARTED_BUDGET_EXHAUSTED"} if allow_not_started else set())
                    or (
                        row["payload_sha256"] is not None
                        and (
                            type(row["payload_sha256"]) is not str
                            or not _SHA.fullmatch(row["payload_sha256"])
                        )
                    )
                    or (
                        row["claim_sha256"] is not None
                        and (
                            type(row["claim_sha256"]) is not str
                            or not _SHA.fullmatch(row["claim_sha256"])
                        )
                    )
                )
            )
        ):
            return False
    return not (
        rows["hours"]["state"] in {"UNKNOWN", "NOT_ATTEMPTED", "NOT_STARTED_BUDGET_EXHAUSTED"}
        and rows["holiday"]["state"] != "NOT_ATTEMPTED"
    )


def _valid_ine_retry_result(result, config, *, allow_capture_stops=False):
    if (
        type(result) is not dict
        or set(result) != {"attempts", "order", "stop_reason", "uncovered_failures"}
        or result["uncovered_failures"] != _INE_RETRY_UNCOVERED
        or result["stop_reason"]
        not in {
            "NONE",
            "RETRY_BUDGET_EXHAUSTED",
            "WAIT_INTERRUPTED",
            "PREVIEW_CLEANUP_UNCERTAIN",
            "WAIT_UNCERTAIN",
            "INE_CLEANUP_OR_RESULT_UNCERTAIN",
            "INE_EVIDENCE_CHANGED_OR_MISSING",
        }
        | ({"CAPTURE_NOT_COMMITTED", "CAPTURE_OUTCOME_UNKNOWN"} if allow_capture_stops else set())
        or type(result["attempts"]) is not dict
        or set(result["attempts"]) != {"hours", "holiday"}
        or type(result["order"]) is not list
    ):
        return False
    expected_order = []
    unknown = False
    not_started = False
    for kind in ("hours", "holiday"):
        rows = result["attempts"][kind]
        ids = config["evidence_ids"][kind]
        if type(rows) is not list or len(rows) > len(ids):
            return False
        for index, row in enumerate(rows):
            if (
                unknown
                or not_started
                or not _valid_ine(
                    {"hours": row, "holiday": _ine_unstarted("0" * 32)},
                    {"hours": ids[index], "holiday": "0" * 32},
                    allow_not_started=True,
                )
            ):
                return False
            if index and not (
                rows[index - 1]["state"] == "FAILED"
                and rows[index - 1]["transport"] == "NETWORK_TIMEOUT"
                and rows[index - 1]["parse"] == "NETWORK_TIMEOUT"
            ):
                return False
            expected_order.append({"kind": kind, "evidence_id": ids[index]})
            unknown = row["state"] == "UNKNOWN"
            not_started = row["state"] == "NOT_STARTED_BUDGET_EXHAUSTED"
            if row["state"] == "NOT_ATTEMPTED":
                return False
    if result["order"] != expected_order:
        return False
    if unknown != (result["stop_reason"] == "INE_CLEANUP_OR_RESULT_UNCERTAIN"):
        return False
    if not_started and result["stop_reason"] != "RETRY_BUDGET_EXHAUSTED":
        return False
    if result["stop_reason"] == "INE_EVIDENCE_CHANGED_OR_MISSING" and not expected_order:
        return False
    return not (
        result["stop_reason"] == "NONE"
        and any(not result["attempts"][kind] for kind in ("hours", "holiday"))
    )


def _base_capture_intent(intent):
    base = dict(intent)
    base["schema"] = base.pop("base_schema")
    base.pop("capture_id")
    return base


def _result(
    run_id, intent, report, ine=None, ine_retry_result=None, capture=None, *, _capture_mode=False
):
    if intent["schema"] == _INTENT_SCHEMA_V6:
        base = _base_capture_intent(intent)
        original = json.loads(
            _result(run_id, base, report, ine, ine_retry_result, _capture_mode=True),
            object_pairs_hook=_unique_pairs,
        )["body"]
        if (
            type(capture) is not dict
            or set(capture) != {"state", "payload_sha256"}
            or capture["state"]
            not in {"COMMITTED", "NOT_COMMITTED", "NOT_ATTEMPTED", "COMMIT_UNKNOWN"}
            or (capture["state"] == "COMMITTED")
            != (
                type(capture["payload_sha256"]) is str
                and _SHA.fullmatch(capture["payload_sha256"]) is not None
            )
            or (capture["state"] != "COMMITTED" and capture["payload_sha256"] is not None)
        ):
            raise ValueError("invalid capture result")
        original.update(
            schema=_RESULT_SCHEMA_V6,
            capture_id=intent["capture_id"],
            capture_state=capture["state"],
            capture_payload_sha256=capture["payload_sha256"],
            capture_association="NOT_PROVEN",
            intent_sha256=hashlib.sha256(_canonical(intent)).hexdigest(),
        )
        return _canonical(
            {"body": original, "sha256": hashlib.sha256(_canonical(original)).hexdigest()}
        )
    candidate_id = intent.get("candidate_id", "")
    data = (
        (_candidate_report(report, candidate_id) if candidate_id else _report_data(report))
        if report is not None
        else None
    )
    if (
        data is not None
        and not intent["shfe_requested"]
        and (
            data["attempts"]
            or data["holiday_attempts"]
            or data["source_status"] != "NOT_REQUESTED"
            or data["holiday_status"] != "NOT_REQUESTED"
        )
    ):
        data = None
    outcome = "UNKNOWN" if data is None else "ERROR" if data["status"] == "ERROR" else "INCOMPLETE"
    body = {
        "schema": _RESULT_SCHEMA,
        "run_id": run_id,
        "shfe_requested": intent["shfe_requested"],
        "preview_outcome": outcome,
        "preview": data,
        "venues": _venues(data, intent["shfe_requested"]),
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "writer_identity": "NOT_PROVEN",
        "time_ordering": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "process_cleanup": "NOT_PROVEN",
        "total_cli_time_bound": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "last_success": "NOT_UPDATED",
        "publication": "NOT_AUTHORIZED",
    }
    if candidate_id:
        body.update(
            schema=_RESULT_SCHEMA_V2,
            candidate_id=candidate_id,
            intent_sha256=hashlib.sha256(_canonical(intent)).hexdigest(),
            candidate_state=_candidate_state(data),
            candidate_association="NOT_PROVEN",
            directory_physical_identity="NOT_PROVEN",
            candidate_authorization="NOT_PROVEN",
        )
    if intent["schema"] == _INTENT_SCHEMA_V3:
        body.update(schema=_RESULT_SCHEMA_V3, navigation_requested=True)
    if intent["schema"] in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
        if intent["schema"] == _INTENT_SCHEMA_V5:
            if not _valid_ine_retry_result(
                ine_retry_result, intent["ine_retry_config"], allow_capture_stops=_capture_mode
            ):
                raise ValueError("invalid INE retries")
            ids = {
                kind: (rows[-1]["evidence_id"] if rows else intent["ine_evidence_ids"][kind])
                for kind, rows in ine_retry_result["attempts"].items()
            }
        else:
            ids = intent["ine_evidence_ids"]
        if not _valid_ine(ine, ids, allow_not_started=True):
            raise ValueError("invalid INE observations")
        body.update(
            schema=_RESULT_SCHEMA_V4,
            navigation_requested=intent["navigation_requested"],
            ine_evidence_ids=intent["ine_evidence_ids"],
            ine_observations=ine,
            ine_retry="D67_20_GAP",
        )
        if intent["schema"] == _INTENT_SCHEMA_V5:
            body.update(
                schema=_RESULT_SCHEMA_V5,
                ine_retry="NETWORK_TIMEOUT_ONLY",
                ine_retry_config=intent["ine_retry_config"],
                ine_retry_result=ine_retry_result,
            )
        states = {item["state"] for item in ine.values()}
        state = (
            "PARTIAL_OBSERVATION"
            if states == {"PARTIAL_OBSERVATION"}
            else "NOT_ATTEMPTED"
            if states == {"NOT_ATTEMPTED"}
            else "UNKNOWN"
            if "UNKNOWN" in states
            else "FAILED"
            if "FAILED" in states
            else "UNKNOWN"
        )
        body["venues"][1] = {
            "venue": "INE",
            "state": state,
            "reason": "UNVERIFIED_PREVIEW",
            "hours": ine["hours"],
            "holiday": ine["holiday"],
        }
    return _canonical({"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()})


def _valid_result(raw, run_id, intent, *, _capture_mode=False):
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(value) is not dict or set(value) != {"body", "sha256"}:
        raise ValueError("invalid result")
    body = value["body"]
    if intent["schema"] == _INTENT_SCHEMA_V6:
        if (
            type(body) is not dict
            or body.get("schema") != _RESULT_SCHEMA_V6
            or body.get("capture_id") != intent["capture_id"]
            or body.get("capture_association") != "NOT_PROVEN"
            or body.get("intent_sha256") != hashlib.sha256(_canonical(intent)).hexdigest()
            or body.get("capture_state")
            not in {"COMMITTED", "NOT_COMMITTED", "NOT_ATTEMPTED", "COMMIT_UNKNOWN"}
            or (body.get("capture_state") == "COMMITTED")
            != (
                type(body.get("capture_payload_sha256")) is str
                and _SHA.fullmatch(body["capture_payload_sha256"]) is not None
            )
            or (
                body.get("capture_state") != "COMMITTED"
                and body.get("capture_payload_sha256") is not None
            )
            or type(value["sha256"]) is not str
            or _SHA.fullmatch(value["sha256"]) is None
            or hashlib.sha256(_canonical(body)).hexdigest() != value["sha256"]
        ):
            raise ValueError("invalid capture result")
        base_intent = _base_capture_intent(intent)
        base = dict(body)
        for name in (
            "capture_id",
            "capture_state",
            "capture_payload_sha256",
            "capture_association",
        ):
            base.pop(name)
        base["schema"] = {
            _INTENT_SCHEMA: _RESULT_SCHEMA,
            _INTENT_SCHEMA_V2: _RESULT_SCHEMA_V2,
            _INTENT_SCHEMA_V3: _RESULT_SCHEMA_V3,
            _INTENT_SCHEMA_V4: _RESULT_SCHEMA_V4,
            _INTENT_SCHEMA_V5: _RESULT_SCHEMA_V5,
        }[base_intent["schema"]]
        if base_intent.get("candidate_id"):
            base["intent_sha256"] = hashlib.sha256(_canonical(base_intent)).hexdigest()
        else:
            base.pop("intent_sha256")
        _valid_result(
            _canonical({"body": base, "sha256": hashlib.sha256(_canonical(base)).hexdigest()}),
            run_id,
            base_intent,
            _capture_mode=True,
        )
        if body["capture_state"] in {"COMMITTED", "NOT_COMMITTED"}:
            preview = body.get("preview")
            if (
                type(preview) is not dict
                or preview.get("input_status") != "CHECKED"
                or preview.get("input_code") != "SUPPLEMENT_UNVERIFIED"
                or preview.get("supplement_status") != "CANDIDATE"
                or preview.get("reason") in _UNCERTAIN | _UNSTARTED
                or (
                    body["capture_state"] == "COMMITTED"
                    and preview.get("reason") == "INPUT_CHECK_FAILED"
                )
                or (
                    body["capture_state"] == "NOT_COMMITTED"
                    and preview.get("reason") != "INPUT_CHECK_FAILED"
                )
            ):
                raise ValueError("capture state unsupported by preview")
        return body
    fields = {
        "schema",
        "run_id",
        "shfe_requested",
        "preview_outcome",
        "preview",
        "venues",
        "verification",
        "source_authentication",
        "writer_identity",
        "time_ordering",
        "durability",
        "process_cleanup",
        "total_cli_time_bound",
        "coverage",
        "last_success",
        "publication",
    }
    candidate_id = intent.get("candidate_id", "")
    if candidate_id:
        fields |= {
            "candidate_id",
            "intent_sha256",
            "candidate_state",
            "candidate_association",
            "directory_physical_identity",
            "candidate_authorization",
        }
    if intent["schema"] in {_INTENT_SCHEMA_V3, _INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
        fields.add("navigation_requested")
    if intent["schema"] in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
        fields |= {"ine_evidence_ids", "ine_observations", "ine_retry"}
    if intent["schema"] == _INTENT_SCHEMA_V5:
        fields |= {"ine_retry_config", "ine_retry_result"}
    if (
        type(body) is not dict
        or set(body) != fields
        or body["schema"]
        != {
            _INTENT_SCHEMA: _RESULT_SCHEMA,
            _INTENT_SCHEMA_V2: _RESULT_SCHEMA_V2,
            _INTENT_SCHEMA_V3: _RESULT_SCHEMA_V3,
            _INTENT_SCHEMA_V4: _RESULT_SCHEMA_V4,
            _INTENT_SCHEMA_V5: _RESULT_SCHEMA_V5,
        }[intent["schema"]]
        or (intent["schema"] == _INTENT_SCHEMA_V3 and body["navigation_requested"] is not True)
        or (
            intent["schema"] == _INTENT_SCHEMA_V4
            and (
                body["navigation_requested"] is not intent["navigation_requested"]
                or body["ine_evidence_ids"] != intent["ine_evidence_ids"]
                or body["ine_retry"] != "D67_20_GAP"
                or not _valid_ine(
                    body["ine_observations"], intent["ine_evidence_ids"], allow_not_started=True
                )
            )
        )
        or (
            intent["schema"] == _INTENT_SCHEMA_V5
            and (
                body["navigation_requested"] is not intent["navigation_requested"]
                or body["ine_evidence_ids"] != intent["ine_evidence_ids"]
                or body["ine_retry"] != "NETWORK_TIMEOUT_ONLY"
                or body["ine_retry_config"] != intent["ine_retry_config"]
                or not _valid_ine_retry_result(
                    body["ine_retry_result"],
                    intent["ine_retry_config"],
                    allow_capture_stops=_capture_mode,
                )
                or body["ine_observations"]
                != {
                    kind: rows[-1] if rows else _ine_unstarted(intent["ine_evidence_ids"][kind])
                    for kind, rows in body["ine_retry_result"]["attempts"].items()
                }
            )
        )
        or body["run_id"] != run_id
        or body["shfe_requested"] is not intent["shfe_requested"]
        or tuple(
            body[k]
            for k in (
                "verification",
                "source_authentication",
                "writer_identity",
                "time_ordering",
                "durability",
                "process_cleanup",
                "total_cli_time_bound",
                "coverage",
                "last_success",
                "publication",
            )
        )
        != (
            "UNVERIFIED",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_PROVEN",
            "NOT_UPDATED",
            "NOT_AUTHORIZED",
        )
        or type(value["sha256"]) is not str
        or _SHA.fullmatch(value["sha256"]) is None
        or hashlib.sha256(_canonical(body)).hexdigest() != value["sha256"]
    ):
        raise ValueError("invalid result")
    preview = body["preview"]
    if preview is not None and _rehydrate(preview, candidate_id) is None:
        raise ValueError("invalid preview")
    if candidate_id and (
        body["candidate_id"] != candidate_id
        or body["intent_sha256"] != hashlib.sha256(_canonical(intent)).hexdigest()
        or body["candidate_state"] != _candidate_state(preview)
        or any(
            body[k] != "NOT_PROVEN"
            for k in (
                "candidate_association",
                "directory_physical_identity",
                "candidate_authorization",
            )
        )
        or preview is not None
        and preview["candidate_status"] != body["candidate_state"]
    ):
        raise ValueError("invalid candidate observation")
    if (
        preview is not None
        and not intent["shfe_requested"]
        and (
            preview["attempts"]
            or preview["holiday_attempts"]
            or preview["source_status"] != "NOT_REQUESTED"
            or preview["holiday_status"] != "NOT_REQUESTED"
        )
    ):
        raise ValueError("unrequested source")
    outcome = (
        "UNKNOWN" if preview is None else "ERROR" if preview["status"] == "ERROR" else "INCOMPLETE"
    )
    expected_venues = _venues(preview, intent["shfe_requested"])
    if intent["schema"] in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
        ine = body["ine_observations"]
        states = {item["state"] for item in ine.values()}
        state = (
            "PARTIAL_OBSERVATION"
            if states == {"PARTIAL_OBSERVATION"}
            else "NOT_ATTEMPTED"
            if states == {"NOT_ATTEMPTED"}
            else "UNKNOWN"
            if "UNKNOWN" in states
            else "FAILED"
            if "FAILED" in states
            else "UNKNOWN"
        )
        expected_venues[1] = {
            "venue": "INE",
            "state": state,
            "reason": "UNVERIFIED_PREVIEW",
            "hours": ine["hours"],
            "holiday": ine["holiday"],
        }
    if body["preview_outcome"] != outcome or body["venues"] != expected_venues:
        raise ValueError("invalid observations")
    return body


def _event(run_id, *, intent, record, reason):
    return {
        "run_id": run_id,
        "intent_installation": intent,
        "record_installation": record,
        "reason": reason,
        "source_authentication": "NOT_PROVEN",
        "writer_identity": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "process_cleanup": "NOT_PROVEN",
        "total_cli_time_bound": "NOT_PROVEN",
        "last_success": "NOT_UPDATED",
        "publication": "NOT_AUTHORIZED",
        "retry_policy": "RECONCILE_FIRST",
    }


def _valid_run_inputs(
    *,
    root,
    config,
    supplement,
    budget_seconds,
    enable_shfe,
    filesystem,
    run_id,
    candidate_id="",
    fetch_product_navigation=False,
    ine_evidence_ids=None,
    ine_retry_config=None,
    capture_id="",
):
    """Validate the shared run arguments without reading or writing files."""
    return not (
        not _id(run_id)
        or type(candidate_id) is not str
        or (candidate_id and (not _id(candidate_id) or candidate_id == run_id))
        or type(capture_id) is not str
        or (capture_id and (not _id(capture_id) or capture_id in {run_id, candidate_id}))
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or any(type(value) is not str or not value for value in (root, config, supplement))
        or type(enable_shfe) is not bool
        or type(fetch_product_navigation) is not bool
        or (fetch_product_navigation and not candidate_id)
        or (
            ine_evidence_ids is not None
            and (
                type(ine_evidence_ids) is not dict
                or set(ine_evidence_ids) != {"hours", "holiday"}
                or any(not _id(v) for v in ine_evidence_ids.values())
                or len({run_id, candidate_id, *ine_evidence_ids.values()}) != 4
            )
        )
        or not _valid_number(budget_seconds, 0.001, 86400.0)
        or (
            ine_retry_config is not None
            and not _valid_ine_retry_config(
                ine_retry_config, run_id, candidate_id, ine_evidence_ids
            )
        )
        or (capture_id and ine_evidence_ids is not None and capture_id in ine_evidence_ids.values())
        or (
            capture_id
            and ine_retry_config is not None
            and any(capture_id in ids for ids in ine_retry_config["evidence_ids"].values())
        )
    )


def run_once(
    *,
    root,
    config,
    supplement,
    budget_seconds,
    enable_shfe,
    filesystem,
    run_id,
    candidate_id="",
    fetch_product_navigation=False,
    ine_evidence_ids=None,
    ine_retry_config=None,
    capture_id="",
    parent_id="",
    parent_sha256="",
    derived_id="",
    source_texts=None,
    decisions=None,
    mock_year=None,
    emit=None,
    akshare_request=None,
    akshare_observation_id="",
):
    """Perform one reservation and one supervised preview; never retry on ambiguity.

    For fictional isolated input, set ``mock_year`` and supply distinct run,
    capture, parent and derived IDs, plus an ``emit`` callback. Set root,
    config and root to None for a temporary root, or pass an empty private root
    and a matching configuration. Leave supplement and network options disabled.
    The callback receives IDs and the dedicated simulation root before writes;
    the root remains after process exit for read-only inspection. An existing
    run ID must be inspected at that original root rather than retried.
    This mode does not publish or establish production identity isolation.
    An optional explicit AKShare request adds a non-publishable mixed-source
    candidate with an independently allocated observation ID. Its shared
    monotonic budget gates observation invocation and caps its child timeout;
    local filesystem work is not a hard deadline.
    """
    if mock_year is not None:
        from bt_api_py.trading_calendar_mock_run import run_mock

        deadline = None
        if akshare_request is not None:
            from bt_api_py import trading_calendar_mock_akshare as mock_akshare

            try:
                if not _valid_number(budget_seconds, 0.001, 86400.0):
                    raise ValueError("invalid budget")
                deadline = time.monotonic() + budget_seconds
                mock_akshare.validate_request(akshare_request, year=mock_year)
                if (
                    not _id(akshare_observation_id)
                    or not callable(emit)
                    or akshare_observation_id in {run_id, capture_id, parent_id, derived_id}
                ):
                    raise ValueError("invalid observation ID")
                emit(
                    {
                        "event": "MOCK_AKSHARE_ID_ALLOCATED",
                        "run_id": run_id,
                        "observation_id": akshare_observation_id,
                        "environment": "SIMULATION_ONLY",
                        "budget_seconds": budget_seconds,
                        "configured_timeout_seconds": akshare_request["timeout"],
                    }
                )
            except Exception:
                return {
                    "state": "NOT_ATTEMPTED",
                    "reason": "INVALID_AKSHARE_REQUEST",
                    "run_id": run_id if _id(run_id) else "",
                    "observation_id": akshare_observation_id if _id(akshare_observation_id) else "",
                }
        elif akshare_observation_id:
            return {"state": "NOT_ATTEMPTED", "reason": "INVALID_AKSHARE_REQUEST"}
        mock_budget = budget_seconds
        if deadline is not None:
            mock_budget = deadline - time.monotonic()
            if mock_budget < 0.001:
                reason = "BUDGET_EXHAUSTED" if mock_budget <= 0 else "BUDGET_BELOW_MINIMUM"
                return {
                    "state": "NOT_ATTEMPTED",
                    "reason": "RUN_" + reason,
                    "run_id": run_id,
                    "akshare_observation": {
                        "run_id": run_id,
                        "observation_id": akshare_observation_id,
                        "observation_state": "NOT_ATTEMPTED",
                        "reason": reason,
                    },
                }
        result = run_mock(
            root=root,
            config=config,
            supplement=supplement,
            budget_seconds=mock_budget,
            enable_shfe=enable_shfe,
            filesystem=filesystem,
            run_id=run_id,
            candidate_id=candidate_id,
            fetch_product_navigation=fetch_product_navigation,
            ine_evidence_ids=ine_evidence_ids,
            ine_retry_config=ine_retry_config,
            capture_id=capture_id,
            parent_id=parent_id,
            parent_sha256=parent_sha256,
            derived_id=derived_id,
            source_texts=source_texts,
            decisions=decisions,
            year=mock_year,
            emit=emit,
        )
        if akshare_request is not None:
            if result.get("candidate_state") == "COMMITTED":
                result["akshare_observation"] = mock_akshare.observe_once(
                    root=result["simulation_root"],
                    run_id=run_id,
                    observation_id=akshare_observation_id,
                    year=mock_year,
                    request=akshare_request,
                    filesystem=filesystem,
                    deadline_monotonic=deadline,
                )
            else:
                result["akshare_observation"] = {
                    "observation_id": akshare_observation_id,
                    "run_id": run_id,
                    "observation_state": "NOT_ATTEMPTED",
                }
        return result
    if akshare_request is not None or akshare_observation_id:
        return {"state": "NOT_ATTEMPTED", "reason": "AKSHARE_REQUIRES_MOCK_MODE"}
    candidate_state = "NOT_ATTEMPTED"
    capture_state, capture_digest = "NOT_ATTEMPTED", None
    cleanup_reason = ""
    evidence_uncertain = False
    retry_stop_reason = ""

    def event(event_run_id, *, intent, record, reason):
        value = _event(event_run_id, intent=intent, record=record, reason=reason)
        if _id(candidate_id):
            value.update(
                candidate_id=candidate_id,
                candidate_state=candidate_state,
                candidate_association="NOT_PROVEN",
                directory_physical_identity="NOT_PROVEN",
                candidate_authorization="NOT_PROVEN",
            )
        if _id(capture_id):
            value.update(
                capture_id=capture_id,
                capture_state=capture_state,
                capture_payload_sha256=capture_digest,
                capture_association="NOT_PROVEN",
            )
            value["capture_outcome"] = {
                "COMMITTED": "CAPTURED_UNVERIFIED",
                "NOT_COMMITTED": "NOT_COMMITTED",
                "NOT_ATTEMPTED": "NOT_ATTEMPTED",
                "COMMIT_UNKNOWN": "UNKNOWN",
            }.get(capture_state, "UNKNOWN")
            if value["reason"] == "NONE" and capture_state != "COMMITTED":
                value["reason"] = {
                    "NOT_COMMITTED": "CAPTURE_NOT_COMMITTED",
                    "NOT_ATTEMPTED": "CAPTURE_NOT_ATTEMPTED",
                }.get(capture_state, "CAPTURE_OUTCOME_UNKNOWN")
        if fetch_product_navigation is True:
            value["navigation_requested"] = True
        if type(ine_evidence_ids) is dict:
            value["ine_evidence_ids"] = ine_evidence_ids
        if type(ine_retry_config) is dict:
            value["ine_retry_config"] = ine_retry_config
        if retry_stop_reason:
            value["ine_retry_stop_reason"] = retry_stop_reason
        if evidence_uncertain:
            value["ine_outcome"] = "UNKNOWN"
            if value["reason"] == "NONE":
                value["reason"] = "INE_EVIDENCE_CHANGED_OR_MISSING"
        if cleanup_reason:
            value.update(cleanup_status="UNCERTAIN")
            if cleanup_reason == "INE_CLEANUP_UNCERTAIN":
                value["ine_outcome"] = "UNKNOWN"
            if value["reason"] == "NONE":
                value["reason"] = cleanup_reason
        return value

    if not _valid_run_inputs(
        root=root,
        config=config,
        supplement=supplement,
        budget_seconds=budget_seconds,
        enable_shfe=enable_shfe,
        filesystem=filesystem,
        run_id=run_id,
        candidate_id=candidate_id,
        fetch_product_navigation=fetch_product_navigation,
        ine_evidence_ids=ine_evidence_ids,
        ine_retry_config=ine_retry_config,
        capture_id=capture_id,
    ):
        return event(
            run_id if _id(run_id) else "",
            intent="NOT_ATTEMPTED",
            record="NOT_ATTEMPTED",
            reason="INVALID_ARGUMENTS",
        )
    if (
        parent_id
        or parent_sha256
        or derived_id
        or source_texts is not None
        or decisions is not None
    ):
        from bt_api_py.trading_calendar_observation_derived import run_associated

        return run_associated(
            base_kwargs={
                "root": root,
                "config": config,
                "supplement": supplement,
                "budget_seconds": budget_seconds,
                "enable_shfe": enable_shfe,
                "filesystem": filesystem,
                "run_id": run_id,
                "candidate_id": candidate_id,
                "fetch_product_navigation": fetch_product_navigation,
                "ine_evidence_ids": ine_evidence_ids,
                "ine_retry_config": ine_retry_config,
                "capture_id": capture_id,
            },
            parent_id=parent_id,
            parent_sha256=parent_sha256,
            derived_id=derived_id,
            source_texts={} if source_texts is None else source_texts,
            decisions=[] if decisions is None else decisions,
        )
    deadline = time.monotonic() + budget_seconds
    try:
        descriptors, chain = _open_runs(root, create=True)
    except Exception:
        return event(
            run_id, intent="NOT_ATTEMPTED", record="NOT_ATTEMPTED", reason="DIRECTORY_UNAVAILABLE"
        )
    directory = descriptors[-1]
    reservation_installed = False
    reservation_attempted = False
    try:
        try:
            if _read_entry(directory, chain, f"run-{run_id}.json") is not None:
                return event(
                    run_id, intent="NOT_COMMITTED", record="NOT_ATTEMPTED", reason="ID_EXISTS"
                )
        except Exception:
            return event(
                run_id,
                intent="NOT_ATTEMPTED",
                record="NOT_ATTEMPTED",
                reason="DIRECTORY_OR_ID_UNKNOWN",
            )
        intent_raw = _intent(
            run_id,
            enable_shfe,
            candidate_id,
            fetch_product_navigation,
            ine_evidence_ids,
            ine_retry_config,
            capture_id,
        )
        reservation_attempted = True
        intent_status, intent_reason = _write_once(
            directory, chain, f"run-{run_id}.intent", intent_raw
        )
        if intent_status != "COMMITTED":
            return event(run_id, intent=intent_status, record="NOT_ATTEMPTED", reason=intent_reason)
        reservation_installed = True
        intent = _valid_intent(intent_raw, run_id)
        if candidate_id and not _directory_matches(chain):
            return event(
                run_id, intent="COMMITTED", record="NOT_ATTEMPTED", reason="DIRECTORY_CHANGED"
            )
        try:
            values = {
                "config_file": config,
                "calendar_root": root,
                "supplement_file": supplement,
                "budget_seconds": budget_seconds
                if ine_evidence_ids is None or ine_retry_config is not None
                else max(0.001, deadline - time.monotonic()),
                "enable_shfe": enable_shfe,
                "gateway_enabled": False,
                "direct_write_enabled": False,
                "max_retries": 2,
                "retry_interval_seconds": 600.0,
            }
            if candidate_id:
                values.update(candidate_id=candidate_id, trusted_local_posix=True)
                candidate_state = "COMMIT_UNKNOWN"
            if capture_id:
                values.update(
                    supplement_capture_id=capture_id,
                    supplement_capture_run_id=run_id,
                    trusted_local_posix=True,
                )
                capture_state = "COMMIT_UNKNOWN"
            if fetch_product_navigation:
                values["fetch_product_navigation"] = True
            report = (
                None
                if ine_evidence_ids is not None
                and ine_retry_config is None
                and time.monotonic() >= deadline
                else _supervise(values)
            )
            if capture_id:
                from bt_api_py.trading_calendar_preview import CapturePreviewResult

                if type(report) is CapturePreviewResult and report.capture_id == capture_id:
                    capture_state, capture_digest, report = (
                        report.capture_state,
                        report.payload_sha256,
                        report.report,
                    )
                    if (
                        type(report) is not PreviewReport
                        or _validated_report(report) is None
                        or report.reason in _UNCERTAIN
                        or (report.reason in _UNSTARTED and capture_state != "NOT_ATTEMPTED")
                        or capture_state
                        not in {"COMMITTED", "NOT_COMMITTED", "NOT_ATTEMPTED", "COMMIT_UNKNOWN"}
                        or (capture_state == "COMMITTED")
                        != (
                            type(capture_digest) is str
                            and _SHA.fullmatch(capture_digest) is not None
                        )
                        or (capture_state != "COMMITTED" and capture_digest is not None)
                        or (
                            capture_state in {"COMMITTED", "NOT_COMMITTED"}
                            and (report.input_status, report.input_code, report.supplement_status)
                            != ("CHECKED", "SUPPLEMENT_UNVERIFIED", "CANDIDATE")
                        )
                        or (capture_state == "COMMITTED" and report.reason == "INPUT_CHECK_FAILED")
                        or (
                            capture_state == "NOT_COMMITTED"
                            and report.reason != "INPUT_CHECK_FAILED"
                        )
                        or (
                            capture_state == "NOT_ATTEMPTED"
                            and report.reason not in _UNSTARTED
                            and report.input_code == "SUPPLEMENT_UNVERIFIED"
                        )
                    ):
                        capture_state, capture_digest = "COMMIT_UNKNOWN", None
                        if type(report) is not PreviewReport:
                            report = None
                    if capture_state == "COMMITTED":
                        from bt_api_py.trading_calendar_supplement_capture import read_capture

                        observed = read_capture(
                            root=root, capture_id=capture_id, filesystem=filesystem
                        )
                        if (
                            observed.get("status") != "VISIBLE_UNAUTHENTICATED"
                            or observed.get("run_id") != run_id
                            or observed.get("payload_sha256") != capture_digest
                        ):
                            capture_state, capture_digest = "COMMIT_UNKNOWN", None
                elif type(report) is PreviewReport and report.reason in _UNSTARTED:
                    capture_state, capture_digest = "NOT_ATTEMPTED", None
                else:
                    report = report if type(report) is PreviewReport else None
                    capture_state, capture_digest = "COMMIT_UNKNOWN", None
        except KeyboardInterrupt:
            if capture_id:
                capture_state, capture_digest = "COMMIT_UNKNOWN", None
            return event(
                run_id,
                intent="COMMITTED",
                record="NOT_ATTEMPTED",
                reason="PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN",
            )
        except Exception:
            if capture_id:
                capture_state, capture_digest = "COMMIT_UNKNOWN", None
            report = None
        if candidate_id and not _directory_matches(chain):
            return event(
                run_id, intent="COMMITTED", record="NOT_ATTEMPTED", reason="DIRECTORY_CHANGED"
            )
        ine = None
        retry_result = None
        if ine_evidence_ids is not None:
            preview = _report_data(report, candidate_id) if report is not None else None
            if capture_id and capture_state != "COMMITTED":
                ine = {kind: _ine_unstarted(eid) for kind, eid in ine_evidence_ids.items()}
                if ine_retry_config is not None:
                    retry_result = {
                        "attempts": {"hours": [], "holiday": []},
                        "order": [],
                        "stop_reason": "CAPTURE_OUTCOME_UNKNOWN"
                        if capture_state == "COMMIT_UNKNOWN"
                        else "CAPTURE_NOT_COMMITTED",
                        "uncovered_failures": _INE_RETRY_UNCOVERED[:],
                    }
            elif _preview_cleanup_uncertain(preview):
                ine = {kind: _ine_unstarted(eid) for kind, eid in ine_evidence_ids.items()}
                cleanup_reason = "PREVIEW_CLEANUP_UNCERTAIN"
                if ine_retry_config is not None:
                    retry_result = {
                        "attempts": {"hours": [], "holiday": []},
                        "order": [],
                        "stop_reason": "PREVIEW_CLEANUP_UNCERTAIN",
                        "uncovered_failures": _INE_RETRY_UNCOVERED[:],
                    }
            elif ine_retry_config is not None:
                retry_result = _ine_retry_observations(
                    root,
                    filesystem,
                    ine_retry_config["evidence_ids"],
                    ine_retry_config["budget_seconds"],
                    ine_retry_config["interval_seconds"],
                )
                retry_stop_reason = retry_result["stop_reason"]
                evidence_uncertain = retry_stop_reason == "INE_EVIDENCE_CHANGED_OR_MISSING"
                ine = {
                    kind: rows[-1] if rows else _ine_unstarted(ine_evidence_ids[kind])
                    for kind, rows in retry_result["attempts"].items()
                }
                if retry_result["stop_reason"] in {
                    "INE_CLEANUP_OR_RESULT_UNCERTAIN",
                    "WAIT_INTERRUPTED",
                    "WAIT_UNCERTAIN",
                }:
                    cleanup_reason = "INE_CLEANUP_UNCERTAIN"
            else:
                ine = _ine_observations(
                    root, filesystem, ine_evidence_ids, deadline, _deadline_capture=True
                )
                if any(item["state"] == "UNKNOWN" for item in ine.values()):
                    cleanup_reason = "INE_CLEANUP_UNCERTAIN"
        result_raw = _result(
            run_id,
            intent,
            report,
            ine,
            retry_result,
            {"state": capture_state, "payload_sha256": capture_digest} if capture_id else None,
        )
        result_body = _valid_result(result_raw, run_id, intent)
        if candidate_id:
            candidate_state = result_body["candidate_state"]
        result_status, result_reason = _write_once(
            directory, chain, f"run-{run_id}.json", result_raw
        )
        return event(run_id, intent="COMMITTED", record=result_status, reason=result_reason)
    except Exception:
        # Reservation was installed; neither completion nor cleanup is inferred.
        return event(
            run_id,
            intent="COMMITTED"
            if reservation_installed
            else "COMMIT_UNKNOWN"
            if reservation_attempted
            else "NOT_ATTEMPTED",
            record="COMMIT_UNKNOWN" if reservation_installed else "NOT_ATTEMPTED",
            reason="OUTCOME_UNKNOWN",
        )
    finally:
        _close(descriptors)


def _ine_visibility(root, filesystem, observation):
    from bt_api_py import trading_calendar_source_evidence as evidence

    results = {}
    for kind in ("hours", "holiday"):
        state = "UNKNOWN"
        try:
            row = observation["ine_observations"][kind]
            if row["capture"] == "NOT_ATTEMPTED":
                state = "NOT_ATTEMPTED"
            elif row["capture"] == "COMMITTED" and row["payload_sha256"] and row["claim_sha256"]:
                observed = evidence.show(
                    root=root, evidence_id=row["evidence_id"], filesystem=filesystem
                )
                if (
                    row["state"] == "NOT_STARTED_BUDGET_EXHAUSTED"
                    and (
                        observed.get("status"),
                        observed.get("evidence_id"),
                        observed.get("exchange"),
                        observed.get("kind"),
                        observed.get("payload_sha256"),
                    )
                    == (
                        "NOT_STARTED_BUDGET_EXHAUSTED",
                        row["evidence_id"],
                        "INE",
                        kind,
                        row["payload_sha256"],
                    )
                    and _ine_claim_digest(root, filesystem, row["evidence_id"], kind)
                    == row["claim_sha256"]
                ):
                    state = "NOT_STARTED_BUDGET_EXHAUSTED"
                elif (
                    observed.get("status"),
                    observed.get("evidence_id"),
                    observed.get("exchange"),
                    observed.get("kind"),
                    observed.get("payload_sha256"),
                    observed.get("transport"),
                    observed.get("page_status"),
                ) == (
                    "VISIBLE_UNAUTHENTICATED",
                    row["evidence_id"],
                    "INE",
                    kind,
                    row["payload_sha256"],
                    row["transport"],
                    row["parse"],
                ) and _ine_claim_digest(root, filesystem, row["evidence_id"], kind) == row[
                    "claim_sha256"
                ]:
                    state = "VISIBLE_UNAUTHENTICATED"
        except Exception:
            state = "UNKNOWN"
        results[kind] = state
    return results


def _ine_retry_visibility(root, filesystem, intent, observation):
    from bt_api_py import trading_calendar_source_evidence as evidence

    result = {}
    for kind, ids in intent["ine_retry_config"]["evidence_ids"].items():
        rows = observation["ine_retry_result"]["attempts"][kind] if observation else []
        result[kind] = []
        for index, eid in enumerate(ids):
            visibility = "UNKNOWN" if observation is None else "NOT_RECORDED"
            if index < len(rows):
                visibility = _ine_visibility(
                    root, filesystem, {"ine_observations": {kind: rows[index]}}
                )[kind]
            entry = {"evidence_id": eid, "visibility": visibility}
            if visibility == "VISIBLE_UNAUTHENTICATED":
                current = evidence.show(root=root, evidence_id=eid, filesystem=filesystem)
                if (
                    current.get("status") != "VISIBLE_UNAUTHENTICATED"
                    or current.get("payload_sha256") != rows[index]["payload_sha256"]
                    or type(current.get("transport_settings")) is not dict
                ):
                    entry["visibility"] = "UNKNOWN"
                else:
                    entry["transport_settings"] = current["transport_settings"]
            result[kind].append(entry)
    return result


def show_once(*, root, run_id, filesystem, derived_id=""):
    """Read intent and result separately; structural checks do not authenticate."""
    result = {
        "run_id": run_id if _id(run_id) else "",
        "intent": "UNKNOWN",
        "record": "UNKNOWN",
        "observation": None,
        "source_authentication": "NOT_PROVEN",
        "writer_identity": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "time_ordering": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }
    if (
        not _id(run_id)
        or (derived_id and not _id(derived_id))
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
    ):
        return result
    try:
        descriptors, chain = _open_runs(root, create=False)
    except Exception:
        return result
    directory = descriptors[-1]
    try:
        from bt_api_py.trading_calendar_mock_run import read_audit

        audit = read_audit(directory, chain, run_id, root)
        if audit is not None:
            result["mock_audit"] = audit
        intent = None
        try:
            raw = _read_entry(directory, chain, f"run-{run_id}.intent")
            if raw is None:
                result["intent"] = "NOT_OBSERVED"
            else:
                intent = _valid_intent(raw, run_id)
                result["intent"] = "VISIBLE_UNAUTHENTICATED"
                if intent.get("candidate_id"):
                    result.update(
                        candidate_id=intent["candidate_id"],
                        candidate_visibility="UNKNOWN",
                        candidate_association="NOT_PROVEN",
                        directory_physical_identity="NOT_PROVEN",
                        candidate_authorization="NOT_PROVEN",
                    )
                if intent["schema"] == _INTENT_SCHEMA_V6:
                    result.update(
                        capture_id=intent["capture_id"],
                        capture_visibility="UNKNOWN",
                        capture_association="NOT_PROVEN",
                    )
                effective_schema = intent.get("base_schema", intent["schema"])
                if effective_schema in {_INTENT_SCHEMA_V3, _INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
                    result["navigation_requested"] = intent["navigation_requested"]
                if effective_schema in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
                    result["ine_evidence_ids"] = intent["ine_evidence_ids"]
                if effective_schema == _INTENT_SCHEMA_V5:
                    result["ine_retry_config"] = intent["ine_retry_config"]
        except Exception:
            result["intent"] = "UNKNOWN"
        try:
            raw = _read_entry(directory, chain, f"run-{run_id}.json")
            if raw is None:
                result["record"] = "NOT_OBSERVED"
            elif intent is not None:
                result["observation"] = _valid_result(raw, run_id, intent)
                result["record"] = "VISIBLE_UNAUTHENTICATED"
            else:
                result["record"] = "UNKNOWN"
        except Exception:
            result["record"] = "UNKNOWN"
        if intent is not None and intent.get("candidate_id"):
            try:
                from bt_api_py.trading_calendar_candidate_inbox import read_candidate

                if not _directory_matches(chain):
                    raise ValueError("directory changed")
                read_candidate(
                    root=root, candidate_id=intent["candidate_id"], filesystem=filesystem
                )
                if not _directory_matches(chain):
                    raise ValueError("directory changed")
                result["candidate_visibility"] = "VISIBLE_UNAUTHENTICATED"
            except Exception:
                result["candidate_visibility"] = "UNKNOWN"
        if intent is not None and intent["schema"] == _INTENT_SCHEMA_V6:
            try:
                from bt_api_py.trading_calendar_supplement_capture import read_capture

                if not _directory_matches(chain):
                    raise ValueError("directory changed")
                observed = read_capture(
                    root=root, capture_id=intent["capture_id"], filesystem=filesystem
                )
                if (
                    observed.get("status") == "VISIBLE_UNAUTHENTICATED"
                    and observed.get("run_id") == run_id
                    and _directory_matches(chain)
                ):
                    observation = result["observation"]
                    if (
                        observation is None
                        or observation["capture_state"] != "COMMITTED"
                        or observation["capture_payload_sha256"] == observed["payload_sha256"]
                    ):
                        result["capture_visibility"] = "VISIBLE_UNAUTHENTICATED"
                        result["capture_payload_sha256"] = observed["payload_sha256"]
                elif observed.get("status") == "NOT_OBSERVED":
                    result["capture_visibility"] = "NOT_OBSERVED"
            except Exception:
                result["capture_visibility"] = "UNKNOWN"
        if intent is not None and intent.get("base_schema", intent["schema"]) == _INTENT_SCHEMA_V4:
            result["ine_evidence_visibility"] = _ine_visibility(
                root, filesystem, result["observation"]
            )
        if intent is not None and intent.get("base_schema", intent["schema"]) == _INTENT_SCHEMA_V5:
            result["ine_retry_visibility"] = _ine_retry_visibility(
                root, filesystem, intent, result["observation"]
            )
        from bt_api_py.trading_calendar_observation_derived import show_association, show_claim

        association = show_association(root=root, filesystem=filesystem, run_id=run_id)
        if association is not None:
            result["derived_association"] = association
        if derived_id:
            result["derived_claim"] = show_claim(
                root=root, filesystem=filesystem, derived_id=derived_id
            )
        return result
    finally:
        _close(descriptors)


def _scan_run_names(directory, chain, max_entries):
    """Bounded name observation; neither a snapshot nor an authenticity proof."""
    if not _directory_matches(chain):
        raise ValueError("directory changed")
    before = os.fstat(directory)
    if before.st_uid != os.geteuid() or before.st_mode & 0o077:
        raise ValueError("directory permissions changed")
    names = set()
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, start=1):
            if count > max_entries:
                raise OverflowError("entry limit")
            names.add(entry.name)
    after = os.fstat(directory)
    if (_identity(before), before.st_uid, before.st_mtime_ns, before.st_ctime_ns) != (
        _identity(after),
        after.st_uid,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or not _directory_matches(chain):
        raise ValueError("directory changed")
    return names, (_identity(after), after.st_uid, after.st_mtime_ns, after.st_ctime_ns)


def list_runs(*, root, filesystem, max_entries=_LIST_ENTRY_LIMIT):
    """List bounded local observations without claiming an exhaustive snapshot."""
    valid_max_entries = type(max_entries) is int and 1 <= max_entries <= _LIST_ENTRY_MAX
    result = {
        "status": "UNKNOWN",
        "reason": "DIRECTORY_UNAVAILABLE",
        "runs": [],
        "max_entries": max_entries if valid_max_entries else None,
        "enumeration_order": "RUN_ID_LEXICAL",
        "snapshot_completeness": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "writer_identity": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "time_ordering": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "last_success": "NOT_UPDATED",
    }
    if (
        filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or type(root) is not str
        or not root
        or not valid_max_entries
    ):
        result["reason"] = "INVALID_ARGUMENTS"
        return result
    try:
        descriptors, chain = _open_runs(root, create=False)
    except Exception:
        return result
    directory = descriptors[-1]
    try:
        names, initial = _scan_run_names(directory, chain, max_entries)
        ids = set()
        invalid = False
        for name in names:
            if name.startswith("run-") and (name.endswith(".intent") or name.endswith(".json")):
                rid = name[4 : -len(".intent")] if name.endswith(".intent") else name[4:-5]
                if _id(rid):
                    ids.add(rid)
                    continue
            invalid = True
        rows = []
        for rid in sorted(ids):
            row = {"run_id": rid, "intent": "UNKNOWN", "record": "UNKNOWN"}
            intent = None
            try:
                raw = _read_entry(directory, chain, f"run-{rid}.intent")
                if raw is None:
                    row["intent"] = "NOT_OBSERVED"
                else:
                    intent = _valid_intent(raw, rid)
                    row["intent"] = "VISIBLE_UNAUTHENTICATED"
                    if intent.get("candidate_id"):
                        row["candidate_id"] = intent["candidate_id"]
                    if intent["schema"] in {
                        _INTENT_SCHEMA_V3,
                        _INTENT_SCHEMA_V4,
                        _INTENT_SCHEMA_V5,
                    }:
                        row["navigation_requested"] = intent["navigation_requested"]
                    if intent["schema"] in {_INTENT_SCHEMA_V4, _INTENT_SCHEMA_V5}:
                        row["ine_evidence_ids"] = intent["ine_evidence_ids"]
                    if intent["schema"] == _INTENT_SCHEMA_V5:
                        row["ine_retry_config"] = intent["ine_retry_config"]
            except Exception:
                row["intent"] = "UNKNOWN"
            try:
                raw = _read_entry(directory, chain, f"run-{rid}.json")
                if raw is None:
                    row["record"] = "NOT_OBSERVED"
                elif intent is not None:
                    observation = _valid_result(raw, rid, intent)
                    row["record"] = "VISIBLE_UNAUTHENTICATED"
                    if intent["schema"] == _INTENT_SCHEMA_V4:
                        row["ine_evidence_visibility"] = _ine_visibility(
                            root, filesystem, observation
                        )
                    if intent["schema"] == _INTENT_SCHEMA_V5:
                        row["ine_retry_visibility"] = _ine_retry_visibility(
                            root, filesystem, intent, observation
                        )
            except Exception:
                row["record"] = "UNKNOWN"
            if (
                intent is not None
                and intent["schema"] == _INTENT_SCHEMA_V5
                and "ine_retry_visibility" not in row
            ):
                row["ine_retry_visibility"] = _ine_retry_visibility(root, filesystem, intent, None)
            rows.append(row)
        final_names, final = _scan_run_names(directory, chain, max_entries)
        if names != final_names or initial != final:
            result["reason"] = "DIRECTORY_CHANGED"
            return result
        result["runs"] = rows
        if invalid or any(
            row["intent"] != "VISIBLE_UNAUTHENTICATED" or row["record"] != "VISIBLE_UNAUTHENTICATED"
            for row in rows
        ):
            result["reason"] = "ENTRY_UNKNOWN"
        else:
            result["status"] = "OBSERVED_UNAUTHENTICATED"
            result["reason"] = "NONE"
        return result
    except OverflowError:
        result["reason"] = "ENTRY_LIMIT_EXCEEDED"
        return result
    except Exception:
        result["reason"] = "DIRECTORY_CHANGED_OR_UNREADABLE"
        return result
    finally:
        _close(descriptors)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError


def _parse_max_entries(value):
    if re.fullmatch(r"[1-9][0-9]{0,3}", value, flags=re.ASCII) is None:
        raise _UsageError
    result = int(value)
    if result > _LIST_ENTRY_MAX:
        raise _UsageError
    return result


def _detail_collection_run(
    *, root, run_id, candidate_id, run_result, budget, per_request, max_retries, retry_interval
):
    """Explicit CLI handoff; only relay bounded fields and actually emitted IDs."""

    def emit(value):
        print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)

    def not_attempted(reason):
        emit(
            {
                "event": "DETAIL_COLLECTION_NOT_ATTEMPTED",
                "run_id": run_id,
                "candidate_id": candidate_id,
                "reason": reason,
            }
        )

    if (
        type(run_result) is not dict
        or run_result.get("run_id") != run_id
        or run_result.get("candidate_id") != candidate_id
    ):
        not_attempted("RUN_OUTCOME_UNKNOWN")
        return 25
    if run_result.get("reason") == "PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN":
        not_attempted("PREVIEW_CLEANUP_UNCERTAIN")
        return 23
    if (
        run_result.get("ine_outcome") == "UNKNOWN"
        or run_result.get("cleanup_status") == "UNCERTAIN"
    ):
        not_attempted(run_result.get("reason", "INE_CLEANUP_UNCERTAIN"))
        return 23
    if (
        run_result.get("intent_installation") != "COMMITTED"
        or run_result.get("record_installation") != "COMMITTED"
        or run_result.get("candidate_state") != "COMMITTED"
        or run_result.get("reason") != "NONE"
    ):
        not_attempted("RUN_NOT_ELIGIBLE")
        if "UNKNOWN" in {
            run_result.get("intent_installation"),
            run_result.get("record_installation"),
            run_result.get("candidate_state"),
        } or "COMMIT_UNKNOWN" in {
            run_result.get("intent_installation"),
            run_result.get("record_installation"),
            run_result.get("candidate_state"),
        }:
            return 25
        return (
            24
            if run_result.get("record_installation") != "COMMITTED"
            or run_result.get("candidate_state") == "NOT_COMMITTED"
            else 10
        )

    try:
        collection_id = uuid.uuid4().hex
    except (Exception, KeyboardInterrupt, SystemExit):
        not_attempted("COLLECTION_ID_UNAVAILABLE")
        return 25
    if not _id(collection_id) or collection_id in {run_id, candidate_id}:
        not_attempted("COLLECTION_ID_UNAVAILABLE")
        return 25
    emitted = False

    def allocated(value):
        nonlocal emitted
        if emitted or value != {
            "event": "COLLECTION_ID_ALLOCATED",
            "collection_id": collection_id,
            "run_id": run_id,
            "candidate_id": candidate_id,
        }:
            raise ValueError("invalid collection allocation")
        emit(value)
        emitted = True

    def unknown(reason, cleanup=False):
        event = {
            "event": "DETAIL_COLLECTION_UNKNOWN" if emitted else "DETAIL_COLLECTION_NOT_ATTEMPTED",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "reason": reason,
        }
        if emitted:
            event["collection_id"] = collection_id
        if cleanup:
            event["cleanup_status"] = "UNCERTAIN"
        emit(event)
        return 23 if cleanup else 25

    try:
        from bt_api_py.trading_calendar_detail_collection import collect_once

        result = collect_once(
            root=root,
            run_id=run_id,
            filesystem=TRUSTED_LOCAL_POSIX,
            budget_seconds=budget,
            per_request_seconds=per_request,
            collection_id=collection_id,
            emit=allocated,
            retry_timeouts=True,
            max_retries=max_retries,
            retry_interval_seconds=retry_interval,
        )
        if not emitted:
            return unknown("COLLECTION_PRECONDITION_UNAVAILABLE")
        cleanup_hint = type(result) is dict and (
            result.get("cleanup_status") == "UNCERTAIN"
            or result.get("reason") == "BATCH_CLEANUP_UNCERTAIN"
        )
        allowed_fields = {
            "collection_id",
            "run_id",
            "candidate_id",
            "status",
            "reason",
            "source_authentication",
            "association",
            "binding",
            "coverage",
            "durability",
            "process_cleanup",
            "publication",
            "last_success",
            "retry",
            "cleanup_status",
            "batch_reason",
            "observed_claimed_collection_id",
        }
        if (
            type(result) is not dict
            or set(result) - allowed_fields
            or any(
                result.get(k) != v
                for k, v in {
                    "collection_id": collection_id,
                    "run_id": run_id,
                    "candidate_id": candidate_id,
                    "source_authentication": "NOT_PROVEN",
                    "association": "NOT_PROVEN",
                    "binding": "NOT_PROVEN",
                    "coverage": "NOT_PROVEN",
                    "durability": "NOT_PROVEN",
                    "process_cleanup": "NOT_PROVEN",
                    "publication": "NOT_AUTHORIZED",
                    "last_success": "NOT_UPDATED",
                    "retry": "D67_20_TIMEOUT_RETRY",
                }.items()
            )
        ):
            return unknown("COLLECTION_RESULT_UNKNOWN", cleanup=cleanup_hint)
        status, reason = result.get("status"), result.get("reason")
        allowed = {
            "OBSERVED_UNVERIFIED": {"ALL_OBSERVED_UNVERIFIED"},
            "PARTIAL": {
                "BATCH_STOPPED",
                "BUDGET_EXHAUSTED",
                "RETRY_EXHAUSTED",
                "RETRY_BUDGET_EXHAUSTED",
            },
            "ERROR": {"INVALID_ARGUMENTS", "RUN_NOT_ELIGIBLE", "INPUT_UNAVAILABLE"},
            "UNKNOWN": {
                "LOCK_UNAVAILABLE",
                "RUN_CLAIM_UNKNOWN",
                "RUN_ALREADY_CLAIMED",
                "CLAIM_NOT_COMMITTED",
                "CLAIM_COMMIT_UNKNOWN",
                "INTENT_NOT_COMMITTED",
                "INTENT_COMMIT_UNKNOWN",
                "BATCH_INTENT_UNKNOWN",
                "BATCH_NOT_STARTED_BUDGET",
                "BATCH_UNKNOWN",
                "BATCH_RESULT_UNKNOWN",
                "BATCH_CLEANUP_UNCERTAIN",
                "RETRY_INTENT_UNKNOWN",
                "RETRY_NOT_STARTED_BUDGET",
                "RETRY_UNKNOWN",
                "RETRY_RESULT_UNKNOWN",
                "END_UNKNOWN",
                "LOCAL_STATE_UNKNOWN",
            },
        }
        if (
            type(status) is not str
            or type(reason) is not str
            or reason not in allowed.get(status, set())
        ):
            return unknown("COLLECTION_RESULT_UNKNOWN", cleanup=cleanup_hint)
        cleanup = result.get("cleanup_status") == "UNCERTAIN"
        batch_reason = result.get("batch_reason")
        if cleanup and status != "UNKNOWN":
            return unknown("COLLECTION_RESULT_UNKNOWN", cleanup=True)
        if cleanup and (
            type(batch_reason) is not str
            or batch_reason
            not in {
                "CHILD_CLEANUP_UNCERTAIN",
                "COLLECTOR_CLEANUP_UNCERTAIN",
                "WORKER_CLEANUP_UNCERTAIN",
                "WORKER_ERROR",
            }
        ):
            return unknown("COLLECTION_RESULT_UNKNOWN", cleanup=True)
        if reason == "BATCH_CLEANUP_UNCERTAIN" and (not cleanup or status != "UNKNOWN"):
            return unknown("COLLECTION_RESULT_UNKNOWN", cleanup=True)
        if "cleanup_status" in result and not cleanup or "batch_reason" in result and not cleanup:
            return unknown("COLLECTION_RESULT_UNKNOWN")
        value = {
            "event": "DETAIL_COLLECTION_RESULT",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "collection_id": collection_id,
            "status": status,
            "reason": reason,
            "source_authentication": "NOT_PROVEN",
            "association": "NOT_PROVEN",
            "binding": "NOT_PROVEN",
            "coverage": "NOT_PROVEN",
            "durability": "NOT_PROVEN",
            "process_cleanup": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
            "last_success": "NOT_UPDATED",
            "retry": "D67_20_TIMEOUT_RETRY",
        }
        if cleanup:
            value.update(cleanup_status="UNCERTAIN", batch_reason=batch_reason)
        if "observed_claimed_collection_id" in result:
            claimed = result["observed_claimed_collection_id"]
            if reason != "RUN_ALREADY_CLAIMED" or not _id(claimed):
                return unknown("COLLECTION_RESULT_UNKNOWN", cleanup=cleanup)
            value["observed_claimed_collection_id"] = claimed
        emit(value)
        # A detail observation does not complete the six-venue observation run.
        return 23 if cleanup else 25 if status in {"UNKNOWN", "ERROR"} else 10
    except BaseException as exc:
        if not isinstance(exc, Exception) and not isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        cleanup = getattr(exc, "child_cleanup", None) == "WORKER_CLEANUP_UNCERTAIN"
        return unknown(
            "COLLECTION_INTERRUPTED"
            if isinstance(exc, KeyboardInterrupt)
            else "COLLECTION_EXCEPTION",
            cleanup=cleanup,
        )


def main(argv=None):
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--calendar-root")
    run_parser.add_argument("--config")
    run_parser.add_argument("--supplement")
    run_parser.add_argument("--budget-seconds", type=float, required=True)
    run_parser.add_argument("--mock", action="store_true")
    run_parser.add_argument("--mock-year", type=int)
    run_parser.add_argument("--mock-review-mode", choices=("auto", "manual"))
    run_parser.add_argument("--akshare-venue")
    run_parser.add_argument("--akshare-contract")
    run_parser.add_argument("--akshare-date")
    run_parser.add_argument("--akshare-timeout", type=float)
    run_parser.add_argument("--enable-shfe", action="store_true")
    run_parser.add_argument("--enable-ine", action="store_true")
    run_parser.add_argument("--ine-retry-timeouts", action="store_true")
    run_parser.add_argument("--ine-budget-seconds", type=float)
    run_parser.add_argument("--ine-max-retries", type=int)
    run_parser.add_argument("--ine-retry-interval-seconds", type=float)
    run_parser.add_argument("--retain-candidate", action="store_true")
    run_parser.add_argument("--capture-supplement", action="store_true")
    run_parser.add_argument("--parent-id")
    run_parser.add_argument("--parent-sha256")
    run_parser.add_argument("--derived-id")
    run_parser.add_argument("--fetch-product-navigation", action="store_true")
    run_parser.add_argument("--collect-product-details", action="store_true")
    run_parser.add_argument("--detail-budget-seconds", type=float)
    run_parser.add_argument("--detail-per-request-seconds", type=float)
    run_parser.add_argument("--detail-max-retries", type=int)
    run_parser.add_argument("--detail-retry-interval-seconds", type=float)
    run_parser.add_argument("--trusted-local-posix", action="store_true")
    show_parser = sub.add_parser("show")
    show_parser.add_argument("--calendar-root", required=True)
    show_parser.add_argument("--run-id", required=True)
    show_parser.add_argument("--derived-id")
    show_parser.add_argument("--trusted-local-posix", action="store_true")
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--calendar-root", required=True)
    list_parser.add_argument("--trusted-local-posix", action="store_true")
    list_parser.add_argument(
        "--max-entries",
        type=_parse_max_entries,
        default=_LIST_ENTRY_LIMIT,
        help=(
            "per-scan entry limit, 1-2000 (default 200); higher values increase local I/O "
            "and bounded file reads; local I/O has no total time limit"
        ),
    )
    mock_update = sub.add_parser("mock-update")
    mock_update.add_argument("--calendar-root", required=True)
    mock_update.add_argument("--run-id", required=True)
    mock_update.add_argument("--variant", choices=("BASE", "AFTERNOON_CHANGE"), default="BASE")
    mock_update.add_argument("--trusted-local-posix", action="store_true")
    mock_confirm = sub.add_parser("mock-confirm")
    mock_confirm.add_argument("--calendar-root", required=True)
    mock_confirm.add_argument("--run-id", required=True)
    mock_confirm.add_argument("--review-id", required=True)
    mock_confirm.add_argument("--review-sha256", required=True)
    mock_confirm.add_argument("--reviewer", required=True)
    mock_confirm.add_argument("--confirmed-at", required=True)
    mock_confirm.add_argument("--trusted-local-posix", action="store_true")
    mock_show = sub.add_parser("mock-show")
    mock_show.add_argument("--calendar-root", required=True)
    mock_show.add_argument("--run-id", required=True)
    mock_show.add_argument("--review-id")
    mock_show.add_argument("--publication-id")
    mock_show.add_argument("--version-id")
    mock_show.add_argument("--candidate-id")
    mock_show.add_argument("--akshare-observation-id")
    mock_show.add_argument("--trusted-local-posix", action="store_true")
    try:
        args = parser.parse_args(argv)
        if not args.trusted_local_posix:
            raise _UsageError
        if args.command in {"mock-update", "mock-confirm", "mock-show"}:
            from bt_api_py import trading_calendar_mock_cli as mock_cli

            if not _id(args.run_id):
                raise _UsageError
            if args.command == "mock-update":
                result = mock_cli.update(
                    root=args.calendar_root, run_id=args.run_id, variant=args.variant
                )
            elif args.command == "mock-confirm":
                if not _id(args.review_id) or _SHA.fullmatch(args.review_sha256) is None:
                    raise _UsageError
                result = mock_cli.confirm(
                    root=args.calendar_root,
                    run_id=args.run_id,
                    review_id=args.review_id,
                    review_sha256=args.review_sha256,
                    reviewer=args.reviewer,
                    confirmed_at=args.confirmed_at,
                )
            else:
                if (
                    (args.review_id and not _id(args.review_id))
                    or (args.candidate_id and not _id(args.candidate_id))
                    or (args.akshare_observation_id and not _id(args.akshare_observation_id))
                ):
                    raise _UsageError
                result = mock_cli.show(
                    root=args.calendar_root,
                    run_id=args.run_id,
                    review_id=args.review_id,
                    publication_id=args.publication_id,
                    version_id=args.version_id,
                    candidate_id=args.candidate_id,
                    akshare_observation_id=args.akshare_observation_id,
                )
            print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
            if args.command == "mock-show":
                unknown = any(
                    isinstance(value, dict) and value.get("status") == "UNKNOWN"
                    for value in result.values()
                )
                return 10 if unknown else 0
            return mock_cli._result_code(result)
        if args.command == "list":
            result = list_runs(
                root=args.calendar_root,
                filesystem=TRUSTED_LOCAL_POSIX,
                max_entries=args.max_entries,
            )
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0 if result["status"] == "OBSERVED_UNAUTHENTICATED" else 10
        if args.command == "show":
            if not _id(args.run_id) or (args.derived_id and not _id(args.derived_id)):
                raise _UsageError
            result = show_once(
                root=args.calendar_root,
                run_id=args.run_id,
                filesystem=TRUSTED_LOCAL_POSIX,
                derived_id=args.derived_id or "",
            )
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0 if result["intent"] == result["record"] == "VISIBLE_UNAUTHENTICATED" else 10
        if not _valid_number(args.budget_seconds, 0.001, 86400.0):
            raise _UsageError
        if args.mock:
            akshare_inputs = (
                args.akshare_venue,
                args.akshare_contract,
                args.akshare_date,
                args.akshare_timeout,
            )
            wants_akshare = any(value is not None for value in akshare_inputs)
            akshare_request = None
            if wants_akshare:
                if (
                    not all((args.akshare_venue, args.akshare_contract, args.akshare_date))
                    or args.mock_review_mode
                ):
                    raise _UsageError
                from bt_api_py.trading_calendar_mock_akshare import validate_request

                try:
                    requested_date = date.fromisoformat(args.akshare_date)
                    if requested_date.isoformat() != args.akshare_date:
                        raise ValueError("noncanonical date")
                    akshare_request = {
                        "venue": args.akshare_venue,
                        "contract": args.akshare_contract,
                        "requested_date": requested_date,
                        "kind": "future",
                        "timeout": args.akshare_timeout
                        if args.akshare_timeout is not None
                        else 15.0,
                    }
                    validate_request(akshare_request, year=args.mock_year)
                except (TypeError, ValueError, OverflowError):
                    raise _UsageError from None
            if (
                args.mock_year is None
                or not 1901 <= args.mock_year <= 9998
                or any(
                    (
                        args.enable_shfe,
                        args.enable_ine,
                        args.ine_retry_timeouts,
                        args.retain_candidate,
                        args.capture_supplement,
                        args.fetch_product_navigation,
                        args.collect_product_details,
                    )
                )
                or any(
                    value is not None
                    for value in (
                        args.supplement,
                        args.parent_id,
                        args.parent_sha256,
                        args.derived_id,
                        args.ine_budget_seconds,
                        args.ine_max_retries,
                        args.ine_retry_interval_seconds,
                        args.detail_budget_seconds,
                        args.detail_per_request_seconds,
                        args.detail_max_retries,
                        args.detail_retry_interval_seconds,
                    )
                )
            ):
                raise _UsageError
            persistent = args.calendar_root is not None or args.config is not None
            if persistent:
                if not args.calendar_root or not args.config or wants_akshare:
                    raise _UsageError
                from bt_api_py.trading_calendar_mock_run import configured_mode

                try:
                    configured_review_mode = configured_mode(args.calendar_root, args.config)
                except Exception:
                    print('{"status":"ERROR","reason":"SIMULATION_CONFIG_INVALID"}', flush=True)
                    return 2
                if args.mock_review_mode and args.mock_review_mode != configured_review_mode:
                    print(
                        '{"status":"ERROR","reason":"SIMULATION_REVIEW_MODE_CONFLICT"}', flush=True
                    )
                    return 2
                args.mock_review_mode = configured_review_mode
            ids = [uuid.uuid4().hex for _ in range(4)]
            if any(not _id(value) for value in ids) or len(set(ids)) != 4:
                raise _UsageError
            mock_publication_ids = None
            if args.mock_review_mode:
                from bt_api_py import trading_calendar_mock_cli as mock_cli

                mock_publication_ids = mock_cli.allocate(candidate_id=ids[3])
                if mock_publication_ids["review_id"] in set(ids):
                    raise _UsageError
                mock_cli._report(
                    "MOCK_PUBLICATION_IDS_ALLOCATED", run_id=ids[0], **mock_publication_ids
                )
            observation_id = uuid.uuid4().hex if akshare_request is not None else ""
            if observation_id and (not _id(observation_id) or observation_id in ids):
                raise _UsageError
            result = run_once(
                root=args.calendar_root,
                config=args.config,
                supplement=None,
                budget_seconds=args.budget_seconds,
                enable_shfe=False,
                filesystem=TRUSTED_LOCAL_POSIX,
                run_id=ids[0],
                capture_id=ids[1],
                parent_id=ids[2],
                derived_id=ids[3],
                mock_year=args.mock_year,
                emit=lambda event: print(json.dumps(event, sort_keys=True), flush=True),
                akshare_request=akshare_request,
                akshare_observation_id=observation_id,
            )
            print(json.dumps(result, sort_keys=True), flush=True)
            if mock_publication_ids is not None and result.get("candidate_state") == "COMMITTED":
                publication = mock_cli.initial(
                    root=result["simulation_root"],
                    run_id=ids[0],
                    candidate_id=ids[3],
                    mode=args.mock_review_mode,
                    ids=mock_publication_ids,
                )
                print(json.dumps(publication, ensure_ascii=True, sort_keys=True), flush=True)
                return mock_cli._result_code(publication)
            if akshare_request is not None:
                source = result.get("akshare_observation", {})
                if (
                    source.get("descriptor_state") in {"UNKNOWN", "COMMIT_UNKNOWN"}
                    or source.get("observation_state")
                    in {"UNKNOWN", "COMMIT_UNKNOWN", "ATTEMPT_UNKNOWN"}
                    or source.get("observation_status") == "UNKNOWN"
                    or source.get("projection_state") == "UNKNOWN"
                ):
                    return 25
                if (
                    source.get("descriptor_state") != "COMMITTED"
                    or source.get("observation_state") != "COMMITTED"
                ):
                    return 24
            return (
                10
                if result["state"] == "INCOMPLETE" and result.get("candidate_state") == "COMMITTED"
                else 25
                if result["state"] == "COMMIT_UNKNOWN"
                else 24
            )
        if (
            args.mock_year is not None
            or args.mock_review_mode is not None
            or any(
                value is not None
                for value in (
                    args.akshare_venue,
                    args.akshare_contract,
                    args.akshare_date,
                    args.akshare_timeout,
                )
            )
            or not all((args.calendar_root, args.config))
            or args.supplement == ""
        ):
            raise _UsageError
        default_supplement = args.supplement is None
        if default_supplement and not os.path.isabs(args.calendar_root):
            raise _UsageError
        supplement = (
            os.path.join(args.calendar_root, "supplement.yaml")
            if default_supplement
            else args.supplement
        )
        capture_supplement = args.capture_supplement or default_supplement
        if any((args.parent_id, args.parent_sha256, args.derived_id)) and (
            not capture_supplement
            or not _id(args.parent_id)
            or not _id(args.derived_id)
            or type(args.parent_sha256) is not str
            or _SHA.fullmatch(args.parent_sha256) is None
        ):
            raise _UsageError
        if args.fetch_product_navigation and not args.retain_candidate:
            raise _UsageError
        if args.ine_retry_timeouts:
            if (
                not args.enable_ine
                or (
                    args.ine_budget_seconds is not None
                    and not _valid_number(args.ine_budget_seconds, 0.1, 86400.0)
                )
                or (args.ine_max_retries is not None and not 0 <= args.ine_max_retries <= 2)
                or (
                    args.ine_retry_interval_seconds is not None
                    and not _valid_number(args.ine_retry_interval_seconds, 0.0, 86400.0)
                )
            ):
                raise _UsageError
        elif any(
            value is not None
            for value in (
                args.ine_budget_seconds,
                args.ine_max_retries,
                args.ine_retry_interval_seconds,
            )
        ):
            raise _UsageError
        if args.collect_product_details:
            if (
                not args.retain_candidate
                or not args.fetch_product_navigation
                or not _valid_number(args.detail_budget_seconds, 0.1, 86400.0)
                or (
                    args.detail_per_request_seconds is not None
                    and not _valid_number(args.detail_per_request_seconds, 0.1, 60.0)
                )
                or (args.detail_max_retries is not None and not 0 <= args.detail_max_retries <= 2)
                or (
                    args.detail_retry_interval_seconds is not None
                    and not _valid_number(args.detail_retry_interval_seconds, 0, 86400)
                )
            ):
                raise _UsageError
        elif any(
            value is not None
            for value in (
                args.detail_budget_seconds,
                args.detail_per_request_seconds,
                args.detail_max_retries,
                args.detail_retry_interval_seconds,
            )
        ):
            raise _UsageError
        run_id = uuid.uuid4().hex
        if not _id(run_id):
            raise _UsageError
        candidate_id = uuid.uuid4().hex if args.retain_candidate else ""
        if args.retain_candidate and (not _id(candidate_id) or candidate_id == run_id):
            raise _UsageError
        capture_id = uuid.uuid4().hex if capture_supplement else ""
        if capture_id and (not _id(capture_id) or capture_id in {run_id, candidate_id}):
            raise _UsageError
        ine_ids = None
        ine_retry_config = None
        if args.enable_ine:
            if args.ine_retry_timeouts:
                count = 1 + (args.ine_max_retries if args.ine_max_retries is not None else 2)
                all_ids = {
                    kind: [uuid.uuid4().hex for _ in range(count)] for kind in ("hours", "holiday")
                }
                ine_ids = {kind: values[0] for kind, values in all_ids.items()}
                ine_retry_config = {
                    "evidence_ids": all_ids,
                    "budget_seconds": args.ine_budget_seconds
                    if args.ine_budget_seconds is not None
                    else 3000.0,
                    "interval_seconds": args.ine_retry_interval_seconds
                    if args.ine_retry_interval_seconds is not None
                    else 600.0,
                }
                if not _valid_ine_retry_config(ine_retry_config, run_id, candidate_id, ine_ids):
                    raise _UsageError
            else:
                ine_ids = {"hours": uuid.uuid4().hex, "holiday": uuid.uuid4().hex}
            if (
                any(not _id(value) for value in ine_ids.values())
                or len({run_id, candidate_id, *ine_ids.values()}) != 4
            ):
                raise _UsageError
        if capture_id and ine_ids is not None and capture_id in ine_ids.values():
            raise _UsageError
        if (
            capture_id
            and ine_retry_config is not None
            and any(capture_id in ids for ids in ine_retry_config["evidence_ids"].values())
        ):
            raise _UsageError
        # This line precedes directory creation, reservation and any preview.
        allocation = {"event": "RUN_ID_ALLOCATED", "run_id": run_id}
        if candidate_id:
            allocation["candidate_id"] = candidate_id
        if capture_id:
            allocation["capture_id"] = capture_id
        if args.derived_id:
            allocation.update(
                derived_id=args.derived_id,
                parent_id=args.parent_id,
                parent_sha256=args.parent_sha256,
            )
        if args.fetch_product_navigation:
            allocation["navigation_requested"] = True
        if ine_ids is not None:
            allocation["ine_evidence_ids"] = ine_ids
        if ine_retry_config is not None:
            allocation["ine_retry_config"] = ine_retry_config
        print(json.dumps(allocation, sort_keys=True), flush=True)
        try:
            result = run_once(
                root=args.calendar_root,
                config=args.config,
                supplement=supplement,
                budget_seconds=args.budget_seconds,
                enable_shfe=args.enable_shfe,
                filesystem=TRUSTED_LOCAL_POSIX,
                run_id=run_id,
                candidate_id=candidate_id,
                **({"capture_id": capture_id} if capture_id else {}),
                **(
                    {
                        "parent_id": args.parent_id,
                        "parent_sha256": args.parent_sha256,
                        "derived_id": args.derived_id,
                    }
                    if args.derived_id
                    else {}
                ),
                fetch_product_navigation=args.fetch_product_navigation,
                **({"ine_evidence_ids": ine_ids} if ine_ids is not None else {}),
                **({"ine_retry_config": ine_retry_config} if ine_retry_config is not None else {}),
            )
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            if not args.collect_product_details:
                raise
            event = {
                "event": "DETAIL_COLLECTION_NOT_ATTEMPTED",
                "run_id": run_id,
                "candidate_id": candidate_id,
                "reason": "RUN_EXECUTION_UNKNOWN",
            }
            print(json.dumps(event, ensure_ascii=True, sort_keys=True), flush=True)
            return 23 if getattr(exc, "worker_cleanup", None) == "WORKER_CLEANUP_UNCERTAIN" else 25
        print(
            json.dumps(result, ensure_ascii=True, sort_keys=True),
            flush=args.collect_product_details,
        )
        if args.collect_product_details:
            if args.derived_id and result.get("derived_state") != "COMMITTED":
                print(
                    json.dumps(
                        {
                            "event": "DETAIL_COLLECTION_NOT_ATTEMPTED",
                            "run_id": run_id,
                            "candidate_id": candidate_id,
                            "reason": "DERIVED_OUTCOME_UNKNOWN"
                            if "COMMIT_UNKNOWN"
                            in {
                                result.get("derived_state"),
                                result.get("association_intent"),
                                result.get("claim_installation"),
                            }
                            else "DERIVED_NOT_COMMITTED",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return (
                    25
                    if "COMMIT_UNKNOWN"
                    in {
                        result.get("derived_state"),
                        result.get("association_intent"),
                        result.get("claim_installation"),
                    }
                    else 24
                )
            if capture_id and result.get("capture_state") != "COMMITTED":
                print(
                    json.dumps(
                        {
                            "event": "DETAIL_COLLECTION_NOT_ATTEMPTED",
                            "run_id": run_id,
                            "candidate_id": candidate_id,
                            "reason": "CAPTURE_OUTCOME_UNKNOWN"
                            if result.get("capture_state") == "COMMIT_UNKNOWN"
                            else "CAPTURE_NOT_COMMITTED",
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return 25 if result.get("capture_state") == "COMMIT_UNKNOWN" else 24
            return _detail_collection_run(
                root=args.calendar_root,
                run_id=run_id,
                candidate_id=candidate_id,
                run_result=result,
                budget=args.detail_budget_seconds,
                per_request=args.detail_per_request_seconds
                if args.detail_per_request_seconds is not None
                else 10.0,
                max_retries=args.detail_max_retries if args.detail_max_retries is not None else 2,
                retry_interval=args.detail_retry_interval_seconds
                if args.detail_retry_interval_seconds is not None
                else 600.0,
            )
        if result["reason"] == "PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN":
            return 23
        if result.get("ine_outcome") == "UNKNOWN" or result.get("cleanup_status") == "UNCERTAIN":
            return 23
        if (
            result.get("candidate_state") == "COMMIT_UNKNOWN"
            or result.get("capture_state") == "COMMIT_UNKNOWN"
        ):
            return 25
        if args.derived_id and result.get("derived_state") != "COMMITTED":
            return (
                25
                if "COMMIT_UNKNOWN"
                in {
                    result.get("derived_state"),
                    result.get("association_intent"),
                    result.get("claim_installation"),
                }
                else 24
            )
        if "UNKNOWN" in {
            result["intent_installation"],
            result["record_installation"],
        } or "COMMIT_UNKNOWN" in {result["intent_installation"], result["record_installation"]}:
            return 25
        if result.get("candidate_state") == "NOT_COMMITTED":
            return 24
        if capture_id and result.get("capture_state") in {"NOT_COMMITTED", "NOT_ATTEMPTED"}:
            return 24
        return 10 if result["record_installation"] == "COMMITTED" else 24
    except ValueError:
        if "args" in locals() and args.command in {"mock-update", "mock-confirm", "mock-show"}:
            print('{"status":"ERROR","reason":"SIMULATION_ROOT_OR_CONFIG_OR_POLICY_CONFLICT"}')
            return 2
        print('{"status":"ERROR","reason":"INVALID_ARGUMENTS"}')
        return 2
    except Exception:
        print('{"status":"ERROR","reason":"INVALID_ARGUMENTS"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
