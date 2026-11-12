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
and cross-process chronology are not proven. No automatic retry is performed.
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
_LIMIT = 65536
_LIST_ENTRY_LIMIT = 200
_VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")


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


def _intent(run_id, enable_shfe):
    body = {
        "schema": _INTENT_SCHEMA,
        "run_id": run_id,
        "shfe_requested": enable_shfe,
        "verification": "UNVERIFIED",
        "writer_identity": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
    }
    return _canonical({"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()})


def _valid_intent(raw, run_id):
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(value) is not dict or set(value) != {"body", "sha256"}:
        raise ValueError("invalid intent")
    body = value["body"]
    if (
        type(body) is not dict
        or set(body)
        != {"schema", "run_id", "shfe_requested", "verification", "writer_identity", "durability"}
        or body["schema"] != _INTENT_SCHEMA
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


def _report_data(report):
    raw = _validated_report(report)
    if raw is None:
        return None
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    # This run does not create candidate files; any such claim is unsupported.
    if (
        value["candidate_id"]
        or value["candidate_status"] != "NOT_REQUESTED"
        or value["candidate_reason"] != "NONE"
    ):
        return None
    return value


def _rehydrate(value):
    if type(value) is not dict:
        return None
    try:
        data = dict(value)
        data["attempts"] = tuple(Attempt(**row) for row in data["attempts"])
        data["holiday_attempts"] = tuple(Attempt(**row) for row in data["holiday_attempts"])
        report = PreviewReport(**data)
        checked = _report_data(report)
        return checked if checked == value else None
    except Exception:
        return None


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
    if transport == "HTTP_OK" and parse in {"CANDIDATE", "NEEDS_REVIEW"}:
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


def _result(run_id, intent, report):
    data = _report_data(report) if report is not None else None
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
    return _canonical({"body": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()})


def _valid_result(raw, run_id, intent):
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(value) is not dict or set(value) != {"body", "sha256"}:
        raise ValueError("invalid result")
    body = value["body"]
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
    if (
        type(body) is not dict
        or set(body) != fields
        or body["schema"] != _RESULT_SCHEMA
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
    if preview is not None and _rehydrate(preview) is None:
        raise ValueError("invalid preview")
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
    if body["preview_outcome"] != outcome or body["venues"] != _venues(
        preview, intent["shfe_requested"]
    ):
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


def run_once(*, root, config, supplement, budget_seconds, enable_shfe, filesystem, run_id):
    """Perform one reservation and one supervised preview; never retry on ambiguity."""
    if (
        not _id(run_id)
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or any(type(value) is not str or not value for value in (root, config, supplement))
        or type(enable_shfe) is not bool
        or not _valid_number(budget_seconds, 0.001, 86400.0)
    ):
        return _event(
            run_id if _id(run_id) else "",
            intent="NOT_ATTEMPTED",
            record="NOT_ATTEMPTED",
            reason="INVALID_ARGUMENTS",
        )
    try:
        descriptors, chain = _open_runs(root, create=True)
    except Exception:
        return _event(
            run_id, intent="NOT_ATTEMPTED", record="NOT_ATTEMPTED", reason="DIRECTORY_UNAVAILABLE"
        )
    directory = descriptors[-1]
    reservation_installed = False
    reservation_attempted = False
    try:
        try:
            if _read_entry(directory, chain, f"run-{run_id}.json") is not None:
                return _event(
                    run_id, intent="NOT_COMMITTED", record="NOT_ATTEMPTED", reason="ID_EXISTS"
                )
        except Exception:
            return _event(
                run_id,
                intent="NOT_ATTEMPTED",
                record="NOT_ATTEMPTED",
                reason="DIRECTORY_OR_ID_UNKNOWN",
            )
        intent_raw = _intent(run_id, enable_shfe)
        reservation_attempted = True
        intent_status, intent_reason = _write_once(
            directory, chain, f"run-{run_id}.intent", intent_raw
        )
        if intent_status != "COMMITTED":
            return _event(
                run_id, intent=intent_status, record="NOT_ATTEMPTED", reason=intent_reason
            )
        reservation_installed = True
        intent = _valid_intent(intent_raw, run_id)
        try:
            report = _supervise(
                {
                    "config_file": config,
                    "calendar_root": root,
                    "supplement_file": supplement,
                    "budget_seconds": budget_seconds,
                    "enable_shfe": enable_shfe,
                    "gateway_enabled": False,
                    "direct_write_enabled": False,
                    "max_retries": 2,
                    "retry_interval_seconds": 600.0,
                }
            )
        except KeyboardInterrupt:
            return _event(
                run_id,
                intent="COMMITTED",
                record="NOT_ATTEMPTED",
                reason="PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN",
            )
        except Exception:
            report = None
        result_raw = _result(run_id, intent, report)
        _valid_result(result_raw, run_id, intent)
        result_status, result_reason = _write_once(
            directory, chain, f"run-{run_id}.json", result_raw
        )
        return _event(run_id, intent="COMMITTED", record=result_status, reason=result_reason)
    except Exception:
        # Reservation was installed; neither completion nor cleanup is inferred.
        return _event(
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


def show_once(*, root, run_id, filesystem):
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
    if not _id(run_id) or filesystem != TRUSTED_LOCAL_POSIX or not _supported():
        return result
    try:
        descriptors, chain = _open_runs(root, create=False)
    except Exception:
        return result
    directory = descriptors[-1]
    try:
        intent = None
        try:
            raw = _read_entry(directory, chain, f"run-{run_id}.intent")
            if raw is None:
                result["intent"] = "NOT_OBSERVED"
            else:
                intent = _valid_intent(raw, run_id)
                result["intent"] = "VISIBLE_UNAUTHENTICATED"
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
        return result
    finally:
        _close(descriptors)


def _scan_run_names(directory, chain):
    """Bounded name observation; neither a snapshot nor an authenticity proof."""
    if not _directory_matches(chain):
        raise ValueError("directory changed")
    before = os.fstat(directory)
    if before.st_uid != os.geteuid() or before.st_mode & 0o077:
        raise ValueError("directory permissions changed")
    names = set()
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, start=1):
            if count > _LIST_ENTRY_LIMIT:
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


def list_runs(*, root, filesystem):
    """List bounded local observations without claiming an exhaustive snapshot."""
    result = {
        "status": "UNKNOWN",
        "reason": "DIRECTORY_UNAVAILABLE",
        "runs": [],
        "enumeration_order": "RUN_ID_LEXICAL",
        "snapshot_completeness": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "writer_identity": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "time_ordering": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "last_success": "NOT_UPDATED",
    }
    if filesystem != TRUSTED_LOCAL_POSIX or not _supported() or type(root) is not str or not root:
        result["reason"] = "INVALID_ARGUMENTS"
        return result
    try:
        descriptors, chain = _open_runs(root, create=False)
    except Exception:
        return result
    directory = descriptors[-1]
    try:
        names, initial = _scan_run_names(directory, chain)
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
            except Exception:
                row["intent"] = "UNKNOWN"
            try:
                raw = _read_entry(directory, chain, f"run-{rid}.json")
                if raw is None:
                    row["record"] = "NOT_OBSERVED"
                elif intent is not None:
                    _valid_result(raw, rid, intent)
                    row["record"] = "VISIBLE_UNAUTHENTICATED"
            except Exception:
                row["record"] = "UNKNOWN"
            rows.append(row)
        final_names, final = _scan_run_names(directory, chain)
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


def main(argv=None):
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--calendar-root", required=True)
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--supplement", required=True)
    run_parser.add_argument("--budget-seconds", type=float, required=True)
    run_parser.add_argument("--enable-shfe", action="store_true")
    run_parser.add_argument("--trusted-local-posix", action="store_true")
    show_parser = sub.add_parser("show")
    show_parser.add_argument("--calendar-root", required=True)
    show_parser.add_argument("--run-id", required=True)
    show_parser.add_argument("--trusted-local-posix", action="store_true")
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--calendar-root", required=True)
    list_parser.add_argument("--trusted-local-posix", action="store_true")
    try:
        args = parser.parse_args(argv)
        if not args.trusted_local_posix:
            raise _UsageError
        if args.command == "list":
            result = list_runs(root=args.calendar_root, filesystem=TRUSTED_LOCAL_POSIX)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0 if result["status"] == "OBSERVED_UNAUTHENTICATED" else 10
        if args.command == "show":
            if not _id(args.run_id):
                raise _UsageError
            result = show_once(
                root=args.calendar_root, run_id=args.run_id, filesystem=TRUSTED_LOCAL_POSIX
            )
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0 if result["intent"] == result["record"] == "VISIBLE_UNAUTHENTICATED" else 10
        if not _valid_number(args.budget_seconds, 0.001, 86400.0):
            raise _UsageError
        run_id = uuid.uuid4().hex
        if not _id(run_id):
            raise _UsageError
        # This line precedes directory creation, reservation and any preview.
        print(
            json.dumps({"event": "RUN_ID_ALLOCATED", "run_id": run_id}, sort_keys=True), flush=True
        )
        result = run_once(
            root=args.calendar_root,
            config=args.config,
            supplement=args.supplement,
            budget_seconds=args.budget_seconds,
            enable_shfe=args.enable_shfe,
            filesystem=TRUSTED_LOCAL_POSIX,
            run_id=run_id,
        )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        if result["reason"] == "PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN":
            return 23
        if "UNKNOWN" in {
            result["intent_installation"],
            result["record_installation"],
        } or "COMMIT_UNKNOWN" in {result["intent_installation"], result["record_installation"]}:
            return 25
        return 10 if result["record_installation"] == "COMMITTED" else 24
    except Exception:
        print('{"status":"ERROR","reason":"INVALID_ARGUMENTS"}')
        return 2


if __name__ == "__main__":
    sys.exit(main())
