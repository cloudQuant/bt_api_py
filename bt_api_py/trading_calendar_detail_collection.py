"""Explicit sequential detail observations; no scheduler, retry or publication.

Local hashes and file visibility do not authenticate sources, writers or run
association, prevent ABA, or prove power-loss durability. Local I/O, process
creation and uninterruptible waits are not bounded by the monotonic deadline.
A missing batch result is UNKNOWN; this command never resumes or retries it.
D67-20 retry policy remains a GAP.
Filesystem safety assumes cooperating trusted local POSIX writers. A hostile
replacement between temporary-entry stat and unlink remains a TOCTOU risk;
ABA and power-loss durability are not ruled out. Claims fence only the same run
ID. Lock release after SIGKILL does not prove that descendants have exited.
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
import time
import uuid

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _remove_owned_temp,
    _supported,
)

_INTENT_LIMIT = 1024 * 1024
_RESULT_LIMIT = 300 * 1024
_OUTPUT_LIMIT = 1024 * 1024
_MAX_PATHS = 512
_BATCH_SIZE = 10
_RESERVE = 1.0


def _json(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _envelope(body):
    return _json({"body": body, "sha256": hashlib.sha256(_json(body)).hexdigest()})


def _unwrap(raw):
    obj = json.loads(raw, object_pairs_hook=runs._unique_pairs)
    if (
        type(obj) is not dict
        or set(obj) != {"body", "sha256"}
        or type(obj["body"]) is not dict
        or obj["sha256"] != hashlib.sha256(_json(obj["body"])).hexdigest()
    ):
        raise ValueError("invalid local record")
    return obj["body"], obj["sha256"]


def _valid_claim(raw, run_id, candidate_id):
    claim, _ = _unwrap(raw)
    if (
        set(claim) != {"schema", "run_id", "collection_id", "candidate_id"}
        or claim["schema"] != "bt_api.calendar-detail-collection-claim/1"
        or claim["run_id"] != run_id
        or not runs._id(claim["collection_id"])
        or claim["candidate_id"] != candidate_id
    ):
        raise ValueError("invalid claim")
    return claim


def _open_collection(root, create):
    fds, chain = _open_directory(root)
    parent = fds[-1]
    try:
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("detail-collections", 0o700, dir_fd=parent)
        info = os.stat("detail-collections", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("invalid collection directory")
        fd = os.open(
            "detail-collections", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "detail-collections", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _read(fd, chain, name, limit):
    if not _directory_matches(chain):
        raise ValueError("directory changed")
    try:
        src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        if not _directory_matches(chain):
            raise ValueError("directory changed") from None
        return None
    try:
        before = os.fstat(src)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_mode & 0o077
            or before.st_uid != os.geteuid()
            or before.st_size > limit
        ):
            raise ValueError("invalid file")
        data = bytearray()
        while len(data) <= limit:
            part = os.read(src, limit + 1 - len(data))
            if not part:
                break
            data.extend(part)
        after = os.fstat(src)
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)

        def snapshot(v):
            return (
                v.st_dev,
                v.st_ino,
                v.st_mode,
                v.st_uid,
                v.st_size,
                v.st_mtime_ns,
                v.st_ctime_ns,
            )

        if (
            len(data) != before.st_size
            or snapshot(before) != snapshot(after)
            or snapshot(after) != snapshot(entry)
            or not _directory_matches(chain)
        ):
            raise ValueError("file changed")
        return bytes(data)
    finally:
        os.close(src)


def _write(fd, chain, name, data, limit):
    if type(data) is not bytes or len(data) > limit or not _directory_matches(chain):
        return "NOT_COMMITTED"
    temp, temp_fd, ident, attempted = "", None, None, False
    try:
        temp = f".collection-{uuid.uuid4().hex}.tmp"
        temp_fd = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        os.fchmod(temp_fd, 0o600)
        ident = _identity(os.fstat(temp_fd))
        written = 0
        while written < len(data):
            n = os.write(temp_fd, data[written:])
            if n <= 0:
                raise OSError("short write")
            written += n
        os.fsync(temp_fd)
        if (
            not _directory_matches(chain)
            or _identity(os.fstat(temp_fd)) != ident
            or _identity(os.stat(temp, dir_fd=fd, follow_symlinks=False)) != ident
        ):
            raise ValueError("directory changed")
        attempted = True
        try:
            os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            old = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if _identity(old) == ident or not _directory_matches(chain):
                raise
            attempted = False
            return "NOT_COMMITTED"
        if (
            _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != ident
            or _read(fd, chain, name, limit) != data
            or _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != ident
            or _identity(os.fstat(temp_fd)) != ident
            or _identity(os.stat(temp, dir_fd=fd, follow_symlinks=False)) != ident
        ):
            raise ValueError("readback mismatch")
        _remove_owned_temp(fd, temp, ident)
        try:
            os.stat(temp, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("temporary entry changed or remained")
        ident = None
        os.fsync(fd)
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return "COMMITTED"
    except Exception:
        return "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED"
    finally:
        if temp_fd is not None:
            with contextlib.suppress(OSError):
                os.close(temp_fd)
        if ident is not None:
            _remove_owned_temp(fd, temp, ident)


def _lock(fd, chain):
    lock = os.open(".collection.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("invalid lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _check_lock(fd, chain, lock, _identity(info))
        return lock, _identity(info)
    except BaseException:
        os.close(lock)
        raise


def _check_lock(fd, chain, lock, ident):
    if (
        not _directory_matches(chain)
        or _identity(os.fstat(lock)) != ident
        or _identity(os.stat(".collection.lock", dir_fd=fd, follow_symlinks=False)) != ident
    ):
        raise ValueError("lock changed")


def _paths(candidate, root):
    if candidate["schema"] != "bt_api.calendar-preview-candidate/6":
        raise ValueError("unsupported candidate")
    fetch = candidate["payload"]["navigation_fetch"]
    nav = fetch["navigation"]
    if fetch["status"] != "CANDIDATE" or nav["status"] != "CANDIDATE" or nav["review_reasons"]:
        raise ValueError("navigation unknown")
    links = nav["links"]
    if type(links) is not list or not 1 <= len(links) <= _MAX_PATHS:
        raise ValueError("navigation size")
    paths, seen = [], {}
    for link in links:
        path, kind, label = link["path"], link["kind"], link["label"]
        if detail._kind(path) != kind or detail._sensitive([path, label], root):
            raise ValueError("invalid navigation")
        if path in seen:
            if seen[path] != (kind, label):
                raise ValueError("navigation conflict")
            continue
        seen[path] = (kind, label)
        paths.append(path)
    return paths


def _intent_valid(body, cid, root):
    if (
        type(body) is not dict
        or set(body)
        != {
            "schema",
            "collection_id",
            "run_id",
            "candidate_id",
            "candidate_payload_sha256",
            "paths",
            "batch_size",
            "source_authentication",
            "association",
            "binding",
            "coverage",
            "durability",
            "publication",
            "retry",
        }
        or body["schema"] != "bt_api.calendar-detail-collection-intent/1"
        or body["collection_id"] != cid
        or not runs._id(body["run_id"])
        or not runs._id(body["candidate_id"])
        or body["run_id"] == body["candidate_id"]
        or type(body["candidate_payload_sha256"]) is not str
        or detail._HEX_SHA.fullmatch(body["candidate_payload_sha256"]) is None
        or type(body["batch_size"]) is not int
        or body["batch_size"] != _BATCH_SIZE
        or any(
            body[k] != "NOT_PROVEN"
            for k in ("source_authentication", "association", "binding", "coverage", "durability")
        )
        or body["publication"] != "NOT_AUTHORIZED"
        or body["retry"] != "D67_20_GAP"
    ):
        raise ValueError("invalid intent")
    paths = body["paths"]
    if (
        type(paths) is not list
        or not 1 <= len(paths) <= _MAX_PATHS
        or len(set(paths)) != len(paths)
        or any(detail._kind(path) not in {"future", "option"} for path in paths)
        or detail._sensitive(paths, root)
    ):
        raise ValueError("invalid paths")
    return body


def _batch_name(cid, index, suffix):
    return f"collection-{cid}.batch-{index:03d}.{suffix}"


def _event(cid, rid, candidate_id, status, reason):
    return {
        "collection_id": cid,
        "run_id": rid,
        "candidate_id": candidate_id,
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
        "retry": "D67_20_GAP",
    }


def collect_once(
    *, root, run_id, filesystem, budget_seconds, per_request_seconds, collection_id, emit
):
    if (
        not runs._id(collection_id)
        or not runs._id(run_id)
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or not detail._safe_root(root)
        or not runs._valid_number(budget_seconds, 0.1, 86400)
        or not runs._valid_number(per_request_seconds, 0.1, 60)
    ):
        return _event(
            collection_id if runs._id(collection_id) else "",
            run_id if runs._id(run_id) else "",
            "",
            "ERROR",
            "INVALID_ARGUMENTS",
        )
    deadline = time.monotonic() + budget_seconds
    observed = runs.show_once(root=root, run_id=run_id, filesystem=filesystem)
    record = observed.get("observation")
    candidate_id = observed.get("candidate_id", "")
    if (
        observed.get("intent") != "VISIBLE_UNAUTHENTICATED"
        or observed.get("record") != "VISIBLE_UNAUTHENTICATED"
        or observed.get("navigation_requested") is not True
        or type(record) is not dict
        or record.get("schema") != runs._RESULT_SCHEMA_V3
        or record.get("candidate_id") != candidate_id
        or record.get("candidate_state") != "COMMITTED"
    ):
        return _event(
            collection_id,
            run_id,
            candidate_id if runs._id(candidate_id) else "",
            "ERROR",
            "RUN_NOT_ELIGIBLE",
        )
    emit(
        {
            "event": "COLLECTION_ID_ALLOCATED",
            "collection_id": collection_id,
            "run_id": run_id,
            "candidate_id": candidate_id,
        }
    )
    try:
        candidate = inbox.read_candidate(
            root=root, candidate_id=candidate_id, filesystem=filesystem
        )
        paths = _paths(candidate, root)
        digest = candidate["payload_sha256"]
        body = {
            "schema": "bt_api.calendar-detail-collection-intent/1",
            "collection_id": collection_id,
            "run_id": run_id,
            "candidate_id": candidate_id,
            "candidate_payload_sha256": digest,
            "paths": paths,
            "batch_size": _BATCH_SIZE,
            "source_authentication": "NOT_PROVEN",
            "association": "NOT_PROVEN",
            "binding": "NOT_PROVEN",
            "coverage": "NOT_PROVEN",
            "durability": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
            "retry": "D67_20_GAP",
        }
        _intent_valid(body, collection_id, root)
        encoded = _envelope(body)
        if len(encoded) > _INTENT_LIMIT:
            raise ValueError("intent too large")
        fds, chain = _open_collection(root, True)
    except Exception:
        return _event(collection_id, run_id, candidate_id, "ERROR", "INPUT_UNAVAILABLE")
    fd = fds[-1]
    lock = None
    cleanup_reason = None
    try:
        try:
            lock, lock_id = _lock(fd, chain)
        except Exception:
            return _event(collection_id, run_id, candidate_id, "UNKNOWN", "LOCK_UNAVAILABLE")
        _check_lock(fd, chain, lock, lock_id)
        claim_name = f"run-{run_id}.collection-claim"
        existing = _read(fd, chain, claim_name, _RESULT_LIMIT)
        if existing is not None:
            try:
                claim = _valid_claim(existing, run_id, candidate_id)
            except Exception:
                return _event(collection_id, run_id, candidate_id, "UNKNOWN", "RUN_CLAIM_UNKNOWN")
            event = _event(collection_id, run_id, candidate_id, "UNKNOWN", "RUN_ALREADY_CLAIMED")
            event["observed_claimed_collection_id"] = claim["collection_id"]
            return event
        claim = {
            "schema": "bt_api.calendar-detail-collection-claim/1",
            "run_id": run_id,
            "collection_id": collection_id,
            "candidate_id": candidate_id,
        }
        claim_status = _write(fd, chain, claim_name, _envelope(claim), _RESULT_LIMIT)
        if claim_status != "COMMITTED":
            return _event(collection_id, run_id, candidate_id, "UNKNOWN", "CLAIM_" + claim_status)
        _check_lock(fd, chain, lock, lock_id)
        intent_status = _write(
            fd, chain, f"collection-{collection_id}.intent", encoded, _INTENT_LIMIT
        )
        if intent_status != "COMMITTED":
            return _event(collection_id, run_id, candidate_id, "UNKNOWN", "INTENT_" + intent_status)
        intent_sha = hashlib.sha256(_json(body)).hexdigest()
        reason = "ALL_OBSERVED_UNVERIFIED"
        started = 0

        def unknown(reason):
            event = _event(collection_id, run_id, candidate_id, "UNKNOWN", reason)
            if cleanup_reason is not None:
                event.update(cleanup_status="UNCERTAIN", batch_reason=cleanup_reason)
            return event

        for index in range((len(paths) + _BATCH_SIZE - 1) // _BATCH_SIZE):
            _check_lock(fd, chain, lock, lock_id)
            remaining = deadline - time.monotonic() - _RESERVE
            if remaining < 0.1:
                reason = "BUDGET_EXHAUSTED"
                break
            selected = paths[index * _BATCH_SIZE : (index + 1) * _BATCH_SIZE]
            start = {
                "schema": "bt_api.calendar-detail-collection-batch-intent/1",
                "collection_id": collection_id,
                "intent_sha256": intent_sha,
                "index": index,
            }
            if (
                _write(
                    fd,
                    chain,
                    _batch_name(collection_id, index, "intent"),
                    _envelope(start),
                    _RESULT_LIMIT,
                )
                != "COMMITTED"
            ):
                return _event(
                    collection_id, run_id, candidate_id, "UNKNOWN", "BATCH_INTENT_UNKNOWN"
                )
            started += 1
            try:
                _check_lock(fd, chain, lock, lock_id)
                remaining = deadline - time.monotonic() - _RESERVE
                if remaining < 0.1:
                    return _event(
                        collection_id, run_id, candidate_id, "UNKNOWN", "BATCH_NOT_STARTED_BUDGET"
                    )
                result = detail._supervise(
                    {
                        "root": root,
                        "candidate_id": candidate_id,
                        "paths": selected,
                        "budget": min(60.0, remaining),
                        "per_request": per_request_seconds,
                        "filesystem": filesystem,
                        "expected_candidate_payload_sha256": digest,
                    }
                )
                detail._validate_state(
                    result,
                    candidate_id=candidate_id,
                    paths=selected,
                    root=root,
                    expected_candidate_payload_sha256=digest,
                )
                if result["status"] == "CLEANUP_UNCERTAIN":
                    cleanup_reason = result["reason"]
                if result["status"] == "IN_PROGRESS":
                    raise ValueError("unfinished batch")
                _check_lock(fd, chain, lock, lock_id)
            except Exception:
                return unknown("BATCH_UNKNOWN")
            result_body = {
                "schema": "bt_api.calendar-detail-collection-batch-result/1",
                "collection_id": collection_id,
                "intent_sha256": intent_sha,
                "index": index,
                "result": result,
            }
            if (
                _write(
                    fd,
                    chain,
                    _batch_name(collection_id, index, "json"),
                    _envelope(result_body),
                    _RESULT_LIMIT,
                )
                != "COMMITTED"
            ):
                return unknown("BATCH_RESULT_UNKNOWN")
            if result["status"] != "OBSERVED_UNVERIFIED":
                reason = "BATCH_CLEANUP_UNCERTAIN" if cleanup_reason else "BATCH_STOPPED"
                break
        end = {
            "schema": "bt_api.calendar-detail-collection-end/1",
            "collection_id": collection_id,
            "intent_sha256": intent_sha,
            "started_batches": started,
            "reason": reason,
        }
        _check_lock(fd, chain, lock, lock_id)
        if (
            _write(fd, chain, f"collection-{collection_id}.end", _envelope(end), _RESULT_LIMIT)
            != "COMMITTED"
        ):
            return unknown("END_UNKNOWN")
        event = _event(
            collection_id,
            run_id,
            candidate_id,
            "OBSERVED_UNVERIFIED"
            if reason == "ALL_OBSERVED_UNVERIFIED"
            else "UNKNOWN"
            if cleanup_reason
            else "PARTIAL",
            reason,
        )
        if cleanup_reason is not None:
            event.update(cleanup_status="UNCERTAIN", batch_reason=cleanup_reason)
        return event
    except Exception:
        event = _event(collection_id, run_id, candidate_id, "UNKNOWN", "LOCAL_STATE_UNKNOWN")
        if cleanup_reason is not None:
            event.update(cleanup_status="UNCERTAIN", batch_reason=cleanup_reason)
        return event
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def _show(*, root, collection_id, filesystem, capture=None):
    result = {
        "collection_id": collection_id if runs._id(collection_id) else "",
        "status": "UNKNOWN",
        "reason": "UNAVAILABLE",
        "paths": [],
        "source_authentication": "NOT_PROVEN",
        "association": "NOT_PROVEN",
        "binding": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
        "process_cleanup": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "last_success": "NOT_UPDATED",
        "retry": "D67_20_GAP",
    }
    if (
        not runs._id(collection_id)
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or not detail._safe_root(root)
    ):
        return result
    try:
        fds, chain = _open_collection(root, False)
    except Exception:
        return result
    fd = fds[-1]
    try:
        raw = _read(fd, chain, f"collection-{collection_id}.intent", _INTENT_LIMIT)
        if raw is None:
            result["reason"] = "INTENT_NOT_OBSERVED"
            return result
        body, intent_sha = _unwrap(raw)
        _intent_valid(body, collection_id, root)
        paths = body["paths"]
        result.update(
            run_id=body["run_id"],
            candidate_id=body["candidate_id"],
            candidate_payload_sha256=body["candidate_payload_sha256"],
            reason="INCOMPLETE_OR_UNKNOWN",
        )
        result["paths"] = [{"path": p, "status": "UNKNOWN"} for p in paths]
        claim_raw = _read(fd, chain, f"run-{body['run_id']}.collection-claim", _RESULT_LIMIT)
        if (
            claim_raw is None
            or _valid_claim(claim_raw, body["run_id"], body["candidate_id"])["collection_id"]
            != collection_id
        ):
            raise ValueError("claim missing or inconsistent")
        count = (len(paths) + _BATCH_SIZE - 1) // _BATCH_SIZE
        if capture is not None and capture["index"] >= count:
            capture["reason"] = "BATCH_INDEX_OUT_OF_RANGE"
            return result
        batches = []
        selected_report = None
        invalid = False
        for index in range(count):
            start_raw = _read(fd, chain, _batch_name(collection_id, index, "intent"), _RESULT_LIMIT)
            batch_raw = _read(fd, chain, _batch_name(collection_id, index, "json"), _RESULT_LIMIT)
            if start_raw is None and batch_raw is None:
                batches.append(None)
                continue
            expected_start = {
                "schema": "bt_api.calendar-detail-collection-batch-intent/1",
                "collection_id": collection_id,
                "intent_sha256": intent_sha,
                "index": index,
            }
            start = _unwrap(start_raw)[0] if start_raw is not None else None
            if start != expected_start or type(start.get("index")) is not int or batch_raw is None:
                invalid = True
                batches.append(None)
                continue
            batch, _ = _unwrap(batch_raw)
            if (
                set(batch) != {"schema", "collection_id", "intent_sha256", "index", "result"}
                or batch["schema"] != "bt_api.calendar-detail-collection-batch-result/1"
                or batch["collection_id"] != collection_id
                or batch["intent_sha256"] != intent_sha
                or type(batch["index"]) is not int
                or batch["index"] != index
            ):
                raise ValueError("invalid batch")
            selected = paths[index * _BATCH_SIZE : (index + 1) * _BATCH_SIZE]
            report = detail._validate_state(
                batch["result"],
                candidate_id=body["candidate_id"],
                paths=selected,
                root=root,
                expected_candidate_payload_sha256=body["candidate_payload_sha256"],
            )
            if report["status"] == "IN_PROGRESS":
                raise ValueError("unfinished batch")
            if capture is not None and index == capture["index"]:
                selected_report = report
            if report["status"] == "CLEANUP_UNCERTAIN":
                result.update(
                    reason="BATCH_CLEANUP_UNCERTAIN",
                    cleanup_status="UNCERTAIN",
                    batch_reason=report["reason"],
                )
            batches.append(report)
            for offset, item in enumerate(report["items"]):
                result["paths"][index * _BATCH_SIZE + offset]["status"] = item["status"]
        end_raw = _read(fd, chain, f"collection-{collection_id}.end", _RESULT_LIMIT)
        if end_raw is not None:
            end, _ = _unwrap(end_raw)
            if (
                set(end)
                != {"schema", "collection_id", "intent_sha256", "started_batches", "reason"}
                or end["schema"] != "bt_api.calendar-detail-collection-end/1"
                or end["collection_id"] != collection_id
                or end["intent_sha256"] != intent_sha
                or type(end["started_batches"]) is not int
                or not 0 <= end["started_batches"] <= count
                or end["reason"]
                not in {
                    "ALL_OBSERVED_UNVERIFIED",
                    "BUDGET_EXHAUSTED",
                    "BATCH_STOPPED",
                    "BATCH_CLEANUP_UNCERTAIN",
                }
            ):
                raise ValueError("invalid end")
            started = end["started_batches"]
            if (
                invalid
                or any(x is None for x in batches[:started])
                or any(x is not None for x in batches[started:])
            ):
                raise ValueError("inconsistent end")
            if any(x["status"] != "OBSERVED_UNVERIFIED" for x in batches[: max(0, started - 1)]):
                raise ValueError("invalid batch ordering")
            if end["reason"] == "ALL_OBSERVED_UNVERIFIED" and (
                started != count or any(x["status"] != "OBSERVED_UNVERIFIED" for x in batches)
            ):
                raise ValueError("invalid completion")
            if end["reason"] == "BUDGET_EXHAUSTED" and (
                started >= count
                or any(x["status"] != "OBSERVED_UNVERIFIED" for x in batches[:started])
            ):
                raise ValueError("invalid budget stop")
            if end["reason"] == "BATCH_STOPPED" and (
                started == 0
                or batches[started - 1]["status"] in {"OBSERVED_UNVERIFIED", "CLEANUP_UNCERTAIN"}
            ):
                raise ValueError("invalid batch stop")
            if end["reason"] == "BATCH_CLEANUP_UNCERTAIN":
                if started == 0 or batches[started - 1]["status"] != "CLEANUP_UNCERTAIN":
                    raise ValueError("invalid cleanup stop")
                result.update(
                    cleanup_status="UNCERTAIN", batch_reason=batches[started - 1]["reason"]
                )
            for item in result["paths"][started * _BATCH_SIZE :]:
                item["status"] = "NOT_ATTEMPTED"
            result["status"] = (
                "OBSERVED_UNVERIFIED"
                if end["reason"] == "ALL_OBSERVED_UNVERIFIED"
                else "UNKNOWN"
                if end["reason"] == "BATCH_CLEANUP_UNCERTAIN"
                else "PARTIAL"
            )
            result["reason"] = end["reason"]
        if not _directory_matches(chain) or len(_json(result)) > _OUTPUT_LIMIT:
            raise ValueError("unbounded or changed")
        if capture is not None:
            capture.update(report=selected_report, end_present=end_raw is not None)
        return result
    except Exception:
        result["status"], result["reason"] = "UNKNOWN", "RECORD_INVALID_OR_CHANGED"
        result["paths"] = [{"path": item["path"], "status": "UNKNOWN"} for item in result["paths"]]
        return result
    finally:
        runs._close(fds)


def show(*, root, collection_id, filesystem):
    return _show(root=root, collection_id=collection_id, filesystem=filesystem)


def show_batch(*, root, collection_id, batch_index, filesystem):
    """Read a zero-based batch; local hashes and records remain unauthenticated."""
    valid_index = (
        type(batch_index) is int
        and 0 <= batch_index < (_MAX_PATHS + _BATCH_SIZE - 1) // _BATCH_SIZE
    )
    result = {
        "collection_id": collection_id if runs._id(collection_id) else "",
        "batch_index": batch_index if valid_index else None,
        "status": "UNKNOWN",
        "reason": "INVALID_ARGUMENTS" if not valid_index else "BATCH_UNAVAILABLE",
        "collection_status": "INCOMPLETE_OR_UNKNOWN",
        "source_authentication": "NOT_PROVEN",
        "coverage": "NOT_PROVEN",
        "binding": "NOT_PROVEN",
        "completeness": "NOT_PROVEN",
        "authorization": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
        "durability": "NOT_PROVEN",
        "result": None,
    }
    if not valid_index:
        return result
    capture = {"index": batch_index}
    summary = _show(root=root, collection_id=collection_id, filesystem=filesystem, capture=capture)
    if summary.get("cleanup_status") == "UNCERTAIN":
        result.update(cleanup_status="UNCERTAIN", batch_reason=summary["batch_reason"])
    if "reason" in capture:
        result["reason"] = capture["reason"]
        return result
    report = capture.get("report")
    if report is None:
        return result
    result.update(
        status="VISIBLE_UNAUTHENTICATED",
        reason="BATCH_VALIDATED_UNAUTHENTICATED",
        collection_status=summary["status"] if capture["end_present"] else "INCOMPLETE_OR_UNKNOWN",
        collection_reason=summary["reason"] if capture["end_present"] else "INCOMPLETE_OR_UNKNOWN",
        run_id=summary["run_id"],
        candidate_id=summary["candidate_id"],
        candidate_payload_sha256=summary["candidate_payload_sha256"],
        result=report,
    )
    if len(_json(result)) > _OUTPUT_LIMIT:
        result.update(status="UNKNOWN", reason="OUTPUT_LIMIT", result=None)
    return result


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError


def _parse_batch_index(value):
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdigit()
        or len(value) > 2
        or (len(value) > 1 and value[0] == "0")
    ):
        raise _ArgumentError
    index = int(value)
    if index >= (_MAX_PATHS + _BATCH_SIZE - 1) // _BATCH_SIZE:
        raise _ArgumentError
    return index


def main(argv=None):
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    run = sub.add_parser("run")
    run.add_argument("--calendar-root", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--collection-id")
    run.add_argument("--trusted-local-posix", action="store_true")
    run.add_argument("--budget-seconds", type=float, required=True)
    run.add_argument("--per-request-seconds", type=float, default=10.0)
    lookup = sub.add_parser("show")
    lookup.add_argument("--calendar-root", required=True)
    lookup.add_argument("--collection-id", required=True)
    lookup.add_argument("--trusted-local-posix", action="store_true")
    lookup_batch = sub.add_parser("show-batch")
    lookup_batch.add_argument("--calendar-root", required=True)
    lookup_batch.add_argument("--collection-id", required=True)
    lookup_batch.add_argument(
        "--batch-index", type=_parse_batch_index, required=True, help="zero-based batch index, 0-51"
    )
    lookup_batch.add_argument("--trusted-local-posix", action="store_true")
    try:
        args = parser.parse_args(argv)
        filesystem = TRUSTED_LOCAL_POSIX if args.trusted_local_posix else ""
        if args.command == "show":
            result = show(
                root=args.calendar_root, collection_id=args.collection_id, filesystem=filesystem
            )
        elif args.command == "show-batch":
            result = show_batch(
                root=args.calendar_root,
                collection_id=args.collection_id,
                batch_index=args.batch_index,
                filesystem=filesystem,
            )
        else:
            collection_id = uuid.uuid4().hex if args.collection_id is None else args.collection_id

            def emit(value):
                sys.stdout.buffer.write(_json(value) + b"\n")
                sys.stdout.buffer.flush()

            result = collect_once(
                root=args.calendar_root,
                run_id=args.run_id,
                filesystem=filesystem,
                budget_seconds=args.budget_seconds,
                per_request_seconds=args.per_request_seconds,
                collection_id=collection_id,
                emit=emit,
            )
        output = _json(result)
        if len(output) > _OUTPUT_LIMIT:
            raise ValueError("output size")
        sys.stdout.buffer.write(output + b"\n")
        if args.command == "show-batch" and result.get("cleanup_status") == "UNCERTAIN":
            return 23
        return (
            0
            if result["status"] in {"OBSERVED_UNVERIFIED", "VISIBLE_UNAUTHENTICATED"}
            else 10
            if result["status"] == "PARTIAL"
            else 2
        )
    except Exception:
        sys.stdout.buffer.write(b'{"status":"ERROR","reason":"INVALID_ARGUMENTS"}\n')
        return 2


if __name__ == "__main__":
    sys.exit(main())
