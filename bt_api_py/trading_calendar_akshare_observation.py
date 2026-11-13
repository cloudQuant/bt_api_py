"""Explicit, single-attempt AKShare observation sidecar for trusted POSIX roots.

A caller supplies its run and observation IDs before calling. A durable-looking
claim fences this observation ID before the provider is invoked; an uncertain
write or missing result is inspected with the original IDs, never retried here.
Bounded typed child-IPC rows, provider projections and a time summary are private
internal data. Retained IPC rows are not upstream response bytes. Their digest
and the content digest are consistency checks, not source authentication.
Local hashes and IDs do
not authenticate a source, writer, scope, trading day, validity or coverage.
This module does not read or alter CURRENT, assemble mock data or publish.

Storage relies on cooperating writers on a declared trusted local POSIX host;
ABA, hostile replacement, OS crashes and power-loss durability are unproven.
The provider bounds parent wait and IPC output, not child CPU, memory, response
decompression, uninterruptible waits or descendant lifetime. No timeout grants
permission for an automatic retry. Python callers must not expose private files.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import math
import os
import re
import stat
from dataclasses import asdict
from datetime import date, datetime

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

_INTENT = "bt_api.akshare-observation-intent/1"
_RESULT = "bt_api.akshare-observation-result/2"
_LIMIT = 3 * 1024 * 1024
_CONTRACT = re.compile(r"[A-Za-z]{1,8}[0-9]{3,4}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CAPTURE = re.compile(r"akshare\.capture\.[0-9a-f]{32}\Z", re.ASCII)
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z", re.ASCII)
_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_STATUSES = frozenset(
    {
        "SUCCESS",
        "EMPTY",
        "MISSING_TRADING_TIME",
        "STRUCTURE_CHANGED",
        "DEPENDENCY_UNAVAILABLE",
        "VERSION_MISMATCH",
        "FETCH_ERROR",
        "FETCH_TIMEOUT",
        "UNKNOWN",
    }
)
_TIME_STATUS = frozenset(
    {
        "SYNTAX_CANDIDATE",
        "NEEDS_REVIEW",
        "MISSING_FIELD",
        "DUPLICATE_FIELD",
        "INVALID_FIELD",
        "INVALID_FIELDS",
        "INPUT_TOO_LONG",
    }
)
_REASONS = frozenset(
    {
        "AMBIGUOUS_DAY_LABEL",
        "AMBIGUOUS_DAY_TRANSITION",
        "INFERRED_DAY_TRANSITION",
        "INVALID_TIME_RANGE",
        "LABEL_WITHOUT_RANGE",
        "NO_LABELED_RANGES",
        "OVERLAPPING_RANGES",
        "UNLABELED_RANGE",
        "UNRECOGNIZED_TEXT",
        "UNSAFE_NORMALIZATION",
        "DUPLICATE_VALUES_OMITTED",
    }
)


def _open(root, create):
    fds, chain = _open_directory(root)
    try:
        parent = fds[-1]
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("akshare-observations", 0o700, dir_fd=parent)
        info = os.stat("akshare-observations", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("invalid private directory")
        fd = os.open(
            "akshare-observations", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "akshare-observations", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _arguments(root, observation_id, run_id, filesystem):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and runs._id(observation_id)
        and runs._id(run_id)
        and observation_id != run_id
    )


def _settings(venue, contract, requested_date, timeout):
    return (
        type(venue) is str
        and venue in _VENUES
        and type(contract) is str
        and _CONTRACT.fullmatch(contract) is not None
        and type(requested_date) is date
        and type(timeout) in (int, float)
        and 0 < timeout <= 60
        and math.isfinite(timeout)
    )


def _time_summary(value, capture_id):
    from bt_api_ctp.calendar_time_candidates import TimeTextCandidate

    if (
        type(value) is not TimeTextCandidate
        or value.source_id != capture_id
        or value.verification != "UNVERIFIED"
    ):
        raise ValueError("invalid time candidate")
    ranges = [
        {
            "label": item.label,
            "scope": item.scope,
            "start": item.start,
            "end": item.end,
            "day_transition": item.day_transition,
        }
        for item in value.ranges
    ]
    summary = {
        "status": value.status,
        "ranges": ranges,
        "review_reasons": list(value.review_reasons),
        "uncertain_count": len(value.uncertain_spans),
        "duplicate_count": len(value.duplicate_indexes),
    }
    _validate_time(summary)
    return summary


def _validate_time(value):
    if (
        type(value) is not dict
        or set(value)
        != {"status", "ranges", "review_reasons", "uncertain_count", "duplicate_count"}
        or type(value["status"]) is not str
        or value["status"] not in _TIME_STATUS
        or type(value["ranges"]) is not list
        or len(value["ranges"]) > 512
        or type(value["review_reasons"]) is not list
        or len(value["review_reasons"]) > len(_REASONS)
        or any(type(x) is not str or x not in _REASONS for x in value["review_reasons"])
        or len(set(value["review_reasons"])) != len(value["review_reasons"])
        or any(
            type(value[k]) is not int or not 0 <= value[k] <= 4096
            for k in ("uncertain_count", "duplicate_count")
        )
    ):
        raise ValueError("invalid time summary")
    for row in value["ranges"]:
        if (
            type(row) is not dict
            or set(row) != {"label", "scope", "start", "end", "day_transition"}
            or row["label"] not in {"日盘", "夜盘", "上午", "下午"}
            or row["scope"] != ("NIGHT" if row["label"] == "夜盘" else "DAY")
            or type(row["start"]) is not str
            or not _CLOCK.fullmatch(row["start"])
            or type(row["end"]) is not str
            or not _CLOCK.fullmatch(row["end"])
            or row["day_transition"]
            not in {"SAME_CLOCK_DAY", "EXPLICIT_NEXT_DAY", "INFERRED_NEXT_DAY"}
        ):
            raise ValueError("invalid time range")
    if value["status"] == "SYNTAX_CANDIDATE" and (
        not value["ranges"] or value["review_reasons"] or value["uncertain_count"]
    ):
        raise ValueError("invalid syntax status")


def _validate_fields(fields):
    from bt_api_ctp.calendar_akshare_candidates import _normalize

    if type(fields) is not list or len(fields) > 512:
        raise ValueError("invalid field count")
    rows = []
    for row in fields:
        if type(row) is not dict or set(row) != {"raw_name", "raw_value", "name", "value"}:
            raise ValueError("invalid field")
        for key in ("raw_name", "raw_value", "name", "value"):
            if row[key] is not None and (type(row[key]) is not str or len(row[key]) > 73728):
                raise ValueError("invalid field text")
        if (
            row["name"] != _normalize(row["raw_name"])
            or row["value"] != _normalize(row["raw_value"])
            or any(
                row[key] is not None and len(row[key]) > 4096 for key in ("raw_name", "raw_value")
            )
        ):
            raise ValueError("invalid normalization")
        rows.append((row["raw_name"], row["raw_value"]))
    return rows


def _project(capture, intent, injected):
    from bt_api_ctp.calendar_akshare_candidates import CandidateField, CandidateResult
    from bt_api_ctp.calendar_akshare_fetch import VERSION, AkshareCapture, TimeDiagnostic
    from bt_api_ctp.calendar_time_candidates import parse_time_candidates

    if type(capture) is not AkshareCapture or type(capture.candidate) is not CandidateResult:
        raise ValueError("invalid provider result")
    c = capture.candidate
    if (
        capture.status not in _STATUSES
        or capture.status != c.status
        or c.venue != intent["venue"]
        or c.contract != intent["contract"]
        or c.requested_date.isoformat() != intent["requested_date"]
        or c.source != "akshare.futures_contract_detail"
        or capture.required_version != VERSION
        or capture.observed_version != c.source_version
        or c.verification != "UNVERIFIED"
        or (
            capture.verification,
            capture.authorization,
            capture.coverage,
            capture.descendant_cleanup,
        )
        != ("UNVERIFIED", "NOT_AUTHORIZED", "UNKNOWN", "NOT_PROVEN")
        or type(capture.capture_id) is not str
        or not _CAPTURE.fullmatch(capture.capture_id)
        or type(c.collected_at) is not datetime
        or c.collected_at.utcoffset() is None
        or c.collected_at.utcoffset().total_seconds() != 0
        or type(c.fields) is not tuple
        or len(c.fields) > 512
    ):
        raise ValueError("invalid provider status")
    if injected and c.timeout_enforced:
        raise ValueError("injected timeout not proven")
    if any(type(item) is not CandidateField for item in c.fields):
        raise ValueError("invalid fields")
    if capture.raw_rows is not None and (
        type(capture.raw_rows) is not tuple
        or any(type(row) is not tuple for row in capture.raw_rows)
    ):
        raise ValueError("invalid typed rows")
    fields = [asdict(item) for item in c.fields]
    rows = _validate_fields(fields)
    time_value = capture.time_candidate
    diagnostic = capture.time_diagnostic
    if capture.status == "SUCCESS":
        if time_value is None or diagnostic is not None:
            raise ValueError("invalid time status")
    elif time_value is not None:
        raise ValueError("invalid time status")
    if diagnostic is not None:
        if (
            type(diagnostic) is not TimeDiagnostic
            or diagnostic.status != "UPSTREAM_NOT_SUCCESS"
            or diagnostic.upstream_status != capture.status
        ):
            raise ValueError("invalid diagnostic")
        time_value = diagnostic.candidate
    if bool(c.fields) != (time_value is not None):
        raise ValueError("invalid field projection")
    if (
        time_value is not None
        and parse_time_candidates(source_id=capture.capture_id, fields=rows) != time_value
    ):
        raise ValueError("time parser mismatch")
    content = {
        "fields": fields,
        "raw_rows": [list(row) for row in capture.raw_rows]
        if capture.raw_rows is not None
        else None,
        "time": _time_summary(time_value, capture.capture_id) if time_value else None,
    }
    body = {
        "schema": _RESULT,
        "observation_id": intent["observation_id"],
        "run_id": intent["run_id"],
        "intent_sha256": storage._unwrap(storage._envelope(intent))[1],
        "status": capture.status,
        "transport_status": capture.transport_status,
        "source_id": capture.source_id,
        "capture_id": capture.capture_id,
        "response_digest": capture.response_digest,
        "required_version": capture.required_version,
        "observed_version": capture.observed_version,
        "captured_at": c.collected_at.isoformat(),
        "content": content,
        "content_sha256": hashlib.sha256(storage._json(content)).hexdigest(),
        "data_state": "DATA_RETAINED",
        "classification_check": "REPLAYED"
        if capture.observed_version is not None
        else "NOT_APPLICABLE",
        "automatic_retry": "NOT_PROVEN_SAFE",
        "time_role": "CANDIDATE"
        if capture.time_candidate
        else "DIAGNOSTIC"
        if diagnostic
        else "NONE",
        "timeout_enforced": c.timeout_enforced,
        "direct_child_cleanup": capture.direct_child_cleanup,
        "descendant_cleanup": capture.descendant_cleanup,
        "timeout_retry": capture.timeout_retry,
        "transport_assurance": "INJECTED_UNVERIFIED" if injected else "PROVIDER_SUPERVISED",
        "source_type": "AKSHARE",
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "identity": "UNKNOWN",
        "effective_period": "UNKNOWN",
        "trading_day": "UNKNOWN",
        "coverage": "UNKNOWN",
        "publication": "NOT_AUTHORIZED",
        "association": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
    }
    _validate_result(intent, body)
    if len(storage._envelope(body)) > _LIMIT:
        body.update(
            content=None,
            content_sha256=None,
            data_state="DATA_OMITTED",
            classification_check="NOT_POSSIBLE",
            time_role="OMITTED",
        )
        _validate_result(intent, body)
    return body


def _validate_result(intent, body):
    from bt_api_ctp.calendar_akshare_fetch import VERSION

    keys = {
        "schema",
        "observation_id",
        "run_id",
        "intent_sha256",
        "status",
        "transport_status",
        "source_id",
        "capture_id",
        "response_digest",
        "required_version",
        "observed_version",
        "captured_at",
        "content",
        "content_sha256",
        "data_state",
        "classification_check",
        "automatic_retry",
        "time_role",
        "timeout_enforced",
        "direct_child_cleanup",
        "descendant_cleanup",
        "timeout_retry",
        "transport_assurance",
        "source_type",
        "verification",
        "source_authentication",
        "identity",
        "effective_period",
        "trading_day",
        "coverage",
        "publication",
        "association",
        "durability",
    }
    if type(body) is not dict or set(body) != keys:
        raise ValueError("invalid result")
    if (
        body["schema"] != _RESULT
        or body["observation_id"] != intent["observation_id"]
        or body["run_id"] != intent["run_id"]
        or body["intent_sha256"] != storage._unwrap(storage._envelope(intent))[1]
        or type(body["status"]) is not str
        or body["status"] not in _STATUSES
        or type(body["capture_id"]) is not str
        or not _CAPTURE.fullmatch(body["capture_id"])
        or body["required_version"] != VERSION
        or body["observed_version"] not in {None, VERSION}
        or body["source_type"] != "AKSHARE"
        or body["verification"] != "UNVERIFIED"
        or body["source_authentication"] != "NOT_PROVEN"
        or body["identity"] != "UNKNOWN"
        or body["effective_period"] != "UNKNOWN"
        or body["trading_day"] != "UNKNOWN"
        or body["coverage"] != "UNKNOWN"
        or body["publication"] != "NOT_AUTHORIZED"
        or body["association"] != "NOT_PROVEN"
        or body["durability"] != "NOT_PROVEN"
        or body["descendant_cleanup"] != "NOT_PROVEN"
        or type(body["timeout_enforced"]) is not bool
        or body["transport_assurance"] not in {"INJECTED_UNVERIFIED", "PROVIDER_SUPERVISED"}
        or body["direct_child_cleanup"] not in {"UNKNOWN", "DIRECT_CHILD_REAPED"}
        or body["automatic_retry"] != "NOT_PROVEN_SAFE"
    ):
        raise ValueError("invalid result metadata")
    stamp = datetime.fromisoformat(body["captured_at"])
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("invalid clock")
    status, transport = body["status"], body["transport_status"]
    if transport not in {
        "CHILD_OK",
        "CHILD_TIMEOUT",
        "CHILD_ERROR",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
    }:
        raise ValueError("invalid transport")
    expected = {
        "CHILD_TIMEOUT": {"FETCH_TIMEOUT"},
        "CHILD_ERROR": {"FETCH_ERROR"},
        "CHILD_CLEANUP_UNCERTAIN": {"UNKNOWN"},
        "SUPERVISOR_UNSUPPORTED": {"UNKNOWN"},
        "CHILD_OK": {
            "SUCCESS",
            "EMPTY",
            "MISSING_TRADING_TIME",
            "STRUCTURE_CHANGED",
            "DEPENDENCY_UNAVAILABLE",
            "VERSION_MISMATCH",
            "FETCH_ERROR",
        },
    }
    if status not in expected[transport]:
        raise ValueError("transport mismatch")
    if body["observed_version"] is not None:
        if transport != "CHILD_OK" or status not in {
            "SUCCESS",
            "EMPTY",
            "MISSING_TRADING_TIME",
            "STRUCTURE_CHANGED",
        }:
            raise ValueError("version mismatch")
        if (
            body["source_id"]
            != f"akshare.futures_contract_detail.v1.{intent['venue']}.{intent['contract']}"
            or type(body["response_digest"]) is not str
            or not _HASH.fullmatch(body["response_digest"])
        ):
            raise ValueError("invalid source")
    elif (
        body["source_id"] is not None
        or body["response_digest"] is not None
        or status in {"SUCCESS", "EMPTY", "MISSING_TRADING_TIME"}
    ):
        raise ValueError("unobserved source")
    if body["transport_assurance"] == "INJECTED_UNVERIFIED" and (
        body["timeout_enforced"] or body["direct_child_cleanup"] != "UNKNOWN"
    ):
        raise ValueError("injected assurance")
    if body["timeout_enforced"] and transport not in {"CHILD_OK", "CHILD_TIMEOUT"}:
        raise ValueError("invalid timeout assurance")
    if body["direct_child_cleanup"] == "DIRECT_CHILD_REAPED" and (
        transport != "CHILD_TIMEOUT" or not body["timeout_enforced"]
    ):
        raise ValueError("invalid cleanup")
    if body["timeout_retry"] != (
        "NOT_PROVEN_SAFE" if status in {"FETCH_TIMEOUT", "UNKNOWN"} else "NOT_APPLICABLE"
    ):
        raise ValueError("invalid retry")
    if body["data_state"] == "DATA_OMITTED":
        if (
            body["classification_check"] != "NOT_POSSIBLE"
            or body["content"] is not None
            or body["content_sha256"] is not None
            or body["time_role"] != "OMITTED"
        ):
            raise ValueError("invalid omission")
        return
    if body["data_state"] != "DATA_RETAINED" or body["classification_check"] != (
        "REPLAYED" if body["observed_version"] is not None else "NOT_APPLICABLE"
    ):
        raise ValueError("invalid data state")
    if body["time_role"] not in {"NONE", "CANDIDATE", "DIAGNOSTIC"} or (
        body["time_role"] == "CANDIDATE"
    ) != (status == "SUCCESS"):
        raise ValueError("invalid time role")
    content = body["content"]
    if (
        type(content) is not dict
        or set(content) != {"fields", "raw_rows", "time"}
        or type(content["fields"]) is not list
        or len(content["fields"]) > 512
        or body["content_sha256"] != hashlib.sha256(storage._json(content)).hexdigest()
    ):
        raise ValueError("invalid retained content")
    from bt_api_ctp.calendar_time_candidates import parse_time_candidates

    rows = _validate_fields(content["fields"])
    if rows and body["observed_version"] is None:
        raise ValueError("fields without observed source")
    raw_rows = content["raw_rows"]
    if body["observed_version"] is not None:
        from bt_api_ctp.calendar_akshare_candidates import collect_contract_candidate
        from bt_api_ctp.calendar_akshare_fetch import _Frame, _response_digest, _valid_rows

        if not _valid_rows(raw_rows) or _response_digest(raw_rows) != body["response_digest"]:
            raise ValueError("typed response mismatch")
        replay = collect_contract_candidate(
            venue=intent["venue"],
            contract=intent["contract"],
            requested_date=date.fromisoformat(intent["requested_date"]),
            source_version=body["observed_version"],
            fetch=lambda **_: _Frame(raw_rows),
        )
        if (
            replay.status != status
            or [asdict(field) for field in replay.fields] != content["fields"]
        ):
            raise ValueError("collector mismatch")
    elif raw_rows is not None:
        raise ValueError("unobserved typed rows")
    if (status == "SUCCESS" and not rows) or (status == "EMPTY" and rows):
        raise ValueError("invalid field status")
    if status == "SUCCESS":
        names = [row["name"] for row in content["fields"]]
        if (
            None in names
            or len(names) != len(set(names))
            or "交易时间" not in names
            or not content["fields"][names.index("交易时间")]["value"]
        ):
            raise ValueError("invalid successful fields")
    if bool(rows) != (content["time"] is not None):
        raise ValueError("invalid field time")
    if (content["time"] is None) != (body["time_role"] == "NONE"):
        raise ValueError("invalid time")
    if content["time"] is not None:
        _validate_time(content["time"])
        parsed = parse_time_candidates(source_id=body["capture_id"], fields=rows)
        if _time_summary(parsed, body["capture_id"]) != content["time"]:
            raise ValueError("time mismatch")


def _valid_intent(intent, observation_id, run_id):
    if type(intent) is not dict or set(intent) != {
        "schema",
        "observation_id",
        "run_id",
        "venue",
        "contract",
        "requested_date",
        "timeout",
    }:
        return False
    try:
        requested = date.fromisoformat(intent["requested_date"])
        return (
            intent["schema"] == _INTENT
            and intent["observation_id"] == observation_id
            and intent["run_id"] == run_id
            and requested.isoformat() == intent["requested_date"]
            and _settings(intent["venue"], intent["contract"], requested, intent["timeout"])
        )
    except (ValueError, TypeError, OverflowError):
        return False


def _interrupt(exc, observation_id, run_id, record_state):
    """Annotate, but never consume, process-control exceptions."""
    if not isinstance(exc, Exception):
        with contextlib.suppress(Exception):
            exc.observation_id = observation_id
            exc.run_id = run_id
            exc.record_state = record_state
            exc.observation_status = "UNKNOWN"
            if hasattr(exc, "add_note"):
                exc.add_note("AKSHARE_OBSERVATION_RESULT_UNKNOWN")
        raise exc


def _observe_impl(
    *,
    root,
    observation_id,
    run_id,
    venue,
    contract,
    requested_date,
    timeout,
    filesystem,
    _collector=None,
):
    """Install intent before one call; never retry an existing or uncertain ID.

    COMMITTED means only local record installation. Always call show with the
    same IDs to recheck it; no status updates a trusted source-success clock.
    """
    if not _arguments(root, observation_id, run_id, filesystem) or not _settings(
        venue, contract, requested_date, timeout
    ):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS"}
    try:
        from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

        if _collector is not None and not callable(_collector):
            raise ValueError
        collector = collect_akshare_contract if _collector is None else _collector
        fds, chain = _open(root, True)
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "NOT_COMMITTED")
        return {"status": "NOT_COMMITTED", "reason": "UNAVAILABLE"}
    intent = {
        "schema": _INTENT,
        "observation_id": observation_id,
        "run_id": run_id,
        "venue": venue,
        "contract": contract,
        "requested_date": requested_date.isoformat(),
        "timeout": timeout,
    }
    lock = None
    attempted = False
    try:
        lock, ident = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, ident)
        # An orphan result is never overwritten or promoted into a valid record.
        if storage._read(fds[-1], chain, f"result-{observation_id}.json", _LIMIT) is not None:
            return {"status": "NOT_COMMITTED", "reason": "ID_ALREADY_USED"}
        attempted = True
        claim = storage._write(
            fds[-1], chain, f"intent-{observation_id}.json", storage._envelope(intent), _LIMIT
        )
        if claim != "COMMITTED":
            return {
                "status": claim,
                "reason": "CLAIM_NOT_PROVEN",
                "observation_id": observation_id,
                "run_id": run_id,
            }
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED")
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            "reason": "CLAIM_UNCERTAIN",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)
    try:
        capture = collector(
            venue=venue, contract=contract, requested_date=requested_date, timeout=timeout
        )
        body = _project(capture, intent, _collector is not None)
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "ATTEMPT_UNKNOWN")
        return {
            "status": "ATTEMPT_UNKNOWN",
            "reason": "COLLECTOR_OR_RESULT_UNCERTAIN",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    try:
        fds, chain = _open(root, False)
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "ATTEMPT_UNKNOWN")
        return {
            "status": "ATTEMPT_UNKNOWN",
            "reason": "DIRECTORY_UNAVAILABLE",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    lock = None
    attempted = False
    try:
        lock, ident = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, ident)
        raw = storage._read(fds[-1], chain, f"intent-{observation_id}.json", _LIMIT)
        if raw is None or storage._unwrap(raw)[0] != intent:
            raise ValueError("claim changed")
        attempted = True
        result = storage._write(
            fds[-1], chain, f"result-{observation_id}.json", storage._envelope(body), _LIMIT
        )
        return {
            "status": "ATTEMPT_UNKNOWN" if result == "NOT_COMMITTED" else result,
            "record_state": "ATTEMPT_UNKNOWN" if result == "NOT_COMMITTED" else result,
            "observation_status": body["status"],
            "data_state": body["data_state"],
            "requires_readback": True,
            "observation_id": observation_id,
            "run_id": run_id,
        }
    except BaseException as exc:
        _interrupt(
            exc, observation_id, run_id, "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN"
        )
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
            "record_state": "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
            "observation_status": body["status"],
            "data_state": body["data_state"],
            "requires_readback": True,
            "reason": "RESULT_UNCERTAIN",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def observe(
    *,
    root,
    observation_id,
    run_id,
    venue,
    contract,
    requested_date,
    timeout,
    filesystem,
    _collector=None,
):
    """Make one claimed attempt; inspect the same IDs with show before use.

    Record installation does not establish a successful or fresh source check.
    """
    result = _observe_impl(
        root=root,
        observation_id=observation_id,
        run_id=run_id,
        venue=venue,
        contract=contract,
        requested_date=requested_date,
        timeout=timeout,
        filesystem=filesystem,
        _collector=_collector,
    )
    result.setdefault("record_state", result["status"])
    result.setdefault("observation_status", "UNKNOWN")
    result.setdefault("requires_readback", True)
    if result.get("data_state") == "DATA_OMITTED":
        result["reported_observation_status"] = result["observation_status"]
        result["observation_status"] = "UNKNOWN"
        result["capture_state"] = "CAPTURE_TOO_LARGE"
        result["classification_check"] = "NOT_POSSIBLE"
        result["automatic_retry"] = "NOT_PROVEN_SAFE"
    return result


def show(*, root, observation_id, run_id, filesystem, include_content=False):
    """Inspect original IDs; opt-in content is private internal data."""
    unknown = {"status": "UNKNOWN", "reason": "UNAVAILABLE_OR_CHANGED"}
    if (
        not _arguments(root, observation_id, run_id, filesystem)
        or type(include_content) is not bool
    ):
        return unknown
    try:
        fds, chain = _open(root, False)
        try:
            raw_intent = storage._read(fds[-1], chain, f"intent-{observation_id}.json", _LIMIT)
            raw_result = storage._read(fds[-1], chain, f"result-{observation_id}.json", _LIMIT)
            if raw_intent is None:
                return {"status": "NOT_OBSERVED"} if raw_result is None else unknown
            intent, _ = storage._unwrap(raw_intent)
            if not _valid_intent(intent, observation_id, run_id):
                return unknown
            if raw_result is None:
                return {
                    "status": "ATTEMPT_UNKNOWN",
                    "observation_id": observation_id,
                    "run_id": run_id,
                    "requested_venue": intent["venue"],
                    "requested_contract": intent["contract"],
                    "requested_date": intent["requested_date"],
                    "request_scope": "DECLARED_NOT_RESPONSE_IDENTITY_OR_TRADING_DAY",
                }
            body, digest = storage._unwrap(raw_result)
            _validate_result(intent, body)
            result = {
                key: body[key]
                for key in (
                    "observation_id",
                    "run_id",
                    "status",
                    "transport_status",
                    "source_id",
                    "capture_id",
                    "response_digest",
                    "required_version",
                    "observed_version",
                    "captured_at",
                    "time_role",
                    "data_state",
                    "classification_check",
                    "automatic_retry",
                    "timeout_enforced",
                    "direct_child_cleanup",
                    "descendant_cleanup",
                    "timeout_retry",
                    "transport_assurance",
                    "source_type",
                    "verification",
                    "source_authentication",
                    "identity",
                    "effective_period",
                    "trading_day",
                    "coverage",
                    "publication",
                    "association",
                    "durability",
                )
            }
            result["observation_status"] = result.pop("status")
            if body["data_state"] == "DATA_OMITTED":
                result["reported_observation_status"] = result["observation_status"]
                result["observation_status"] = "UNKNOWN"
                result["capture_state"] = "CAPTURE_TOO_LARGE"
            result["status"] = "VISIBLE_UNAUTHENTICATED"
            result["record_state"] = "VISIBLE_UNAUTHENTICATED"
            result["requested_venue"] = intent["venue"]
            result["requested_contract"] = intent["contract"]
            result["requested_date"] = intent["requested_date"]
            result["request_scope"] = "DECLARED_NOT_RESPONSE_IDENTITY_OR_TRADING_DAY"
            result["payload_sha256"] = digest
            result["content_sha256"] = body["content_sha256"]
            if include_content:
                result["content"] = body["content"]
            return result
        finally:
            runs._close(fds)
    except Exception:
        return unknown
