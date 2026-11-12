"""Explicit sequential detail observations; no scheduler or publication.

Local hashes and file visibility do not authenticate sources, writers or run
association, prevent ABA, or prove power-loss durability. Local I/O, process
creation and uninterruptible waits are not bounded by the monotonic deadline.
A missing result is UNKNOWN; this command never resumes or retries it.
Timeout retries require an explicit flag and remain subject to one deadline.
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
import math
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
_RETRY_RESULT_LIMIT = 64 * 1024
_OUTPUT_LIMIT = 1024 * 1024
_RETRY_OUTPUT_LIMIT = 2 * 1024 * 1024
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
    v2 = type(body) is dict and body.get("schema") == "bt_api.calendar-detail-collection-intent/2"
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
        | (
            {"max_retries", "retry_interval_seconds", "budget_seconds", "per_request_seconds"}
            if v2
            else set()
        )
        or body["schema"]
        not in {
            "bt_api.calendar-detail-collection-intent/1",
            "bt_api.calendar-detail-collection-intent/2",
        }
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
        or body["retry"] != ("D67_20_TIMEOUT_RETRY" if v2 else "D67_20_GAP")
        or (
            v2
            and not _retry_config_valid(
                body["max_retries"],
                body["retry_interval_seconds"],
                body["budget_seconds"],
                body["per_request_seconds"],
            )
        )
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


def _retry_name(cid, path_index, attempt, suffix):
    return f"collection-{cid}.retry-{path_index:03d}-{attempt}.{suffix}"


def _retry_config_valid(max_retries, interval, budget, per_request):
    return (
        type(max_retries) is int
        and 0 <= max_retries <= 2
        and type(interval) in (int, float)
        and math.isfinite(interval)
        and 0 <= interval <= 86400
        and runs._valid_number(budget, 0.1, 86400)
        and runs._valid_number(per_request, 0.1, 60)
    )


def _retryable(item):
    return (
        item["status"] == "UNKNOWN"
        and item["reason"] == "FETCH_FAILED"
        and item["transport_status"] in {"NETWORK_TIMEOUT", "CHILD_TIMEOUT"}
        and item["parse_status"] == "NOT_OBSERVED"
        and item["identity_status"] == "NOT_ASSESSED"
        and item["page_sha256"] is None
        and item["records"] is None
    )


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
    *,
    root,
    run_id,
    filesystem,
    budget_seconds,
    per_request_seconds,
    collection_id,
    emit,
    retry_timeouts=False,
    max_retries=2,
    retry_interval_seconds=600.0,
):
    def outcome(*args):
        value = _event(*args)
        if retry_timeouts is True:
            value["retry"] = "D67_20_TIMEOUT_RETRY"
        return value

    if (
        not runs._id(collection_id)
        or not runs._id(run_id)
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or not detail._safe_root(root)
        or not runs._valid_number(budget_seconds, 0.1, 86400)
        or not runs._valid_number(per_request_seconds, 0.1, 60)
        or type(retry_timeouts) is not bool
        or not _retry_config_valid(
            max_retries, retry_interval_seconds, budget_seconds, per_request_seconds
        )
    ):
        return outcome(
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
        return outcome(
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
            "schema": "bt_api.calendar-detail-collection-intent/2"
            if retry_timeouts
            else "bt_api.calendar-detail-collection-intent/1",
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
            "retry": "D67_20_TIMEOUT_RETRY" if retry_timeouts else "D67_20_GAP",
        }
        if retry_timeouts:
            body.update(
                max_retries=max_retries,
                retry_interval_seconds=retry_interval_seconds,
                budget_seconds=budget_seconds,
                per_request_seconds=per_request_seconds,
            )
        _intent_valid(body, collection_id, root)
        encoded = _envelope(body)
        if len(encoded) > _INTENT_LIMIT:
            raise ValueError("intent too large")
        fds, chain = _open_collection(root, True)
    except Exception:
        return outcome(collection_id, run_id, candidate_id, "ERROR", "INPUT_UNAVAILABLE")
    fd = fds[-1]
    lock = None
    cleanup_reason = None
    try:
        try:
            lock, lock_id = _lock(fd, chain)
        except Exception:
            return outcome(collection_id, run_id, candidate_id, "UNKNOWN", "LOCK_UNAVAILABLE")
        _check_lock(fd, chain, lock, lock_id)
        claim_name = f"run-{run_id}.collection-claim"
        existing = _read(fd, chain, claim_name, _RESULT_LIMIT)
        if existing is not None:
            try:
                claim = _valid_claim(existing, run_id, candidate_id)
            except Exception:
                return outcome(collection_id, run_id, candidate_id, "UNKNOWN", "RUN_CLAIM_UNKNOWN")
            event = outcome(collection_id, run_id, candidate_id, "UNKNOWN", "RUN_ALREADY_CLAIMED")
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
            return outcome(collection_id, run_id, candidate_id, "UNKNOWN", "CLAIM_" + claim_status)
        _check_lock(fd, chain, lock, lock_id)
        intent_status = _write(
            fd, chain, f"collection-{collection_id}.intent", encoded, _INTENT_LIMIT
        )
        if intent_status != "COMMITTED":
            return outcome(
                collection_id, run_id, candidate_id, "UNKNOWN", "INTENT_" + intent_status
            )
        intent_sha = hashlib.sha256(_json(body)).hexdigest()
        reason = "ALL_OBSERVED_UNVERIFIED"
        started = 0

        def unknown(reason):
            event = outcome(collection_id, run_id, candidate_id, "UNKNOWN", reason)
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
                "schema": "bt_api.calendar-detail-collection-batch-intent/2"
                if retry_timeouts
                else "bt_api.calendar-detail-collection-batch-intent/1",
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
                return outcome(
                    collection_id, run_id, candidate_id, "UNKNOWN", "BATCH_INTENT_UNKNOWN"
                )
            started += 1
            try:
                _check_lock(fd, chain, lock, lock_id)
                remaining = deadline - time.monotonic() - _RESERVE
                if remaining < 0.1:
                    return outcome(
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
            except Exception as exc:
                if getattr(exc, "child_cleanup", None) == "WORKER_CLEANUP_UNCERTAIN":
                    cleanup_reason = "WORKER_CLEANUP_UNCERTAIN"
                return unknown("BATCH_UNKNOWN")
            result_body = {
                "schema": "bt_api.calendar-detail-collection-batch-result/2"
                if retry_timeouts
                else "bt_api.calendar-detail-collection-batch-result/1",
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
            if retry_timeouts and result["status"] == "PARTIAL":
                # A completed batch does not expose individual completion times;
                # its persisted return time is a conservative upper bound.
                if any(
                    item["status"] != "CANDIDATE" and not _retryable(item)
                    for item in result["items"]
                ):
                    reason = "BATCH_STOPPED"
                    break
                batch_finished = time.monotonic()
                stop_retries = False
                for offset, initial in enumerate(result["items"]):
                    if not _retryable(initial):
                        continue
                    previous = result
                    finished = batch_finished
                    recovered = False
                    for attempt in range(1, max_retries + 1):
                        target = (
                            finished
                            + retry_interval_seconds
                            + (0.000001 if retry_interval_seconds else 0)
                        )
                        if target + _RESERVE + 0.1 > deadline:
                            reason, stop_retries = "RETRY_BUDGET_EXHAUSTED", True
                            break
                        while True:
                            _check_lock(fd, chain, lock, lock_id)
                            now = time.monotonic()
                            if deadline - now - _RESERVE < 0.1:
                                reason, stop_retries = "RETRY_BUDGET_EXHAUSTED", True
                                break
                            if now >= target:
                                break
                            time.sleep(min(1.0, target - now))
                        if stop_retries:
                            break
                        started_at = time.monotonic()
                        if deadline - started_at - _RESERVE < 0.1:
                            reason, stop_retries = "RETRY_BUDGET_EXHAUSTED", True
                            break
                        path_index = index * _BATCH_SIZE + offset
                        previous_sha = hashlib.sha256(_json(previous)).hexdigest()
                        retry_start = {
                            "schema": "bt_api.calendar-detail-retry-intent/2",
                            "collection_id": collection_id,
                            "intent_sha256": intent_sha,
                            "path_index": path_index,
                            "attempt": attempt,
                            "previous_result_sha256": previous_sha,
                            "previous_finished_ns": int(finished * 1_000_000_000),
                            "started_ns": int(started_at * 1_000_000_000),
                            "clock_authentication": "NOT_PROVEN",
                        }
                        if (
                            _write(
                                fd,
                                chain,
                                _retry_name(collection_id, path_index, attempt, "intent"),
                                _envelope(retry_start),
                                _RETRY_RESULT_LIMIT,
                            )
                            != "COMMITTED"
                        ):
                            return unknown("RETRY_INTENT_UNKNOWN")
                        try:
                            _check_lock(fd, chain, lock, lock_id)
                            remaining = deadline - time.monotonic() - _RESERVE
                            if remaining < 0.1:
                                return unknown("RETRY_NOT_STARTED_BUDGET")
                            retried = detail._supervise(
                                {
                                    "root": root,
                                    "candidate_id": candidate_id,
                                    "paths": [selected[offset]],
                                    "budget": min(60.0, remaining),
                                    "per_request": per_request_seconds,
                                    "filesystem": filesystem,
                                    "expected_candidate_payload_sha256": digest,
                                }
                            )
                            detail._validate_state(
                                retried,
                                candidate_id=candidate_id,
                                paths=[selected[offset]],
                                root=root,
                                expected_candidate_payload_sha256=digest,
                            )
                            if retried["status"] == "CLEANUP_UNCERTAIN":
                                cleanup_reason = retried["reason"]
                            if retried["status"] == "IN_PROGRESS":
                                raise ValueError("unfinished retry")
                            _check_lock(fd, chain, lock, lock_id)
                        except Exception as exc:
                            if getattr(exc, "child_cleanup", None) == "WORKER_CLEANUP_UNCERTAIN":
                                cleanup_reason = "WORKER_CLEANUP_UNCERTAIN"
                            return unknown("RETRY_UNKNOWN")
                        retry_body = {
                            "schema": "bt_api.calendar-detail-retry-result/2",
                            "collection_id": collection_id,
                            "intent_sha256": intent_sha,
                            "path_index": path_index,
                            "attempt": attempt,
                            "retry_intent_sha256": hashlib.sha256(_json(retry_start)).hexdigest(),
                            "result": retried,
                        }
                        if (
                            _write(
                                fd,
                                chain,
                                _retry_name(collection_id, path_index, attempt, "json"),
                                _envelope(retry_body),
                                _RETRY_RESULT_LIMIT,
                            )
                            != "COMMITTED"
                        ):
                            return unknown("RETRY_RESULT_UNKNOWN")
                        previous = retried
                        finished = time.monotonic()
                        if (
                            retried["status"] == "OBSERVED_UNVERIFIED"
                            and retried["items"][0]["status"] == "CANDIDATE"
                        ):
                            recovered = True
                            break
                        if retried["status"] == "CLEANUP_UNCERTAIN":
                            reason, stop_retries = "BATCH_CLEANUP_UNCERTAIN", True
                            break
                        if retried["status"] != "PARTIAL" or not _retryable(retried["items"][0]):
                            reason, stop_retries = "BATCH_STOPPED", True
                            break
                    if stop_retries:
                        break
                    if not recovered:
                        reason, stop_retries = "RETRY_EXHAUSTED", True
                        break
                if stop_retries:
                    break
                continue
            if result["status"] != "OBSERVED_UNVERIFIED":
                reason = "BATCH_CLEANUP_UNCERTAIN" if cleanup_reason else "BATCH_STOPPED"
                break
        end = {
            "schema": "bt_api.calendar-detail-collection-end/2"
            if retry_timeouts
            else "bt_api.calendar-detail-collection-end/1",
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
        event = outcome(
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
        event = outcome(collection_id, run_id, candidate_id, "UNKNOWN", "LOCAL_STATE_UNKNOWN")
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


def _show_v2(fd, chain, body, intent_sha, root, result, capture):
    cid, paths, candidate_id = body["collection_id"], body["paths"], body["candidate_id"]
    count = (len(paths) + _BATCH_SIZE - 1) // _BATCH_SIZE
    if capture is not None and capture["index"] >= count:
        capture["reason"] = "BATCH_INDEX_OUT_OF_RANGE"
        return result
    result["retry"] = "D67_20_TIMEOUT_RETRY"
    result["paths"] = [{"path": path, "status": "UNKNOWN", "attempts": []} for path in paths]
    batches, selected_report, selected_retries = [], None, []
    incomplete = False
    cleanup_reason = None
    for index in range(count):
        start_raw = _read(fd, chain, _batch_name(cid, index, "intent"), _RESULT_LIMIT)
        raw = _read(fd, chain, _batch_name(cid, index, "json"), _RESULT_LIMIT)
        initial = None
        if start_raw is not None or raw is not None:
            expected = {
                "schema": "bt_api.calendar-detail-collection-batch-intent/2",
                "collection_id": cid,
                "intent_sha256": intent_sha,
                "index": index,
            }
            start = _unwrap(start_raw)[0] if start_raw is not None else None
            if start != expected or type(start.get("index")) is not int or raw is None:
                incomplete = True
            else:
                envelope, _ = _unwrap(raw)
                if (
                    set(envelope) != {"schema", "collection_id", "intent_sha256", "index", "result"}
                    or envelope["schema"] != "bt_api.calendar-detail-collection-batch-result/2"
                    or envelope["collection_id"] != cid
                    or envelope["intent_sha256"] != intent_sha
                    or type(envelope["index"]) is not int
                    or envelope["index"] != index
                ):
                    raise ValueError("invalid batch")
                selected = paths[index * _BATCH_SIZE : (index + 1) * _BATCH_SIZE]
                initial = detail._validate_state(
                    envelope["result"],
                    candidate_id=candidate_id,
                    paths=selected,
                    root=root,
                    expected_candidate_payload_sha256=body["candidate_payload_sha256"],
                )
                if initial["status"] == "IN_PROGRESS":
                    raise ValueError("unfinished batch")
                if capture is not None and capture["index"] == index:
                    selected_report = initial
                if initial["status"] == "CLEANUP_UNCERTAIN":
                    cleanup_reason = initial["reason"]
                    result.update(cleanup_status="UNCERTAIN", batch_reason=cleanup_reason)
        batch_items = initial["items"] if initial else None
        batch_cleanup = initial is not None and initial["status"] == "CLEANUP_UNCERTAIN"
        allow_retry = (
            initial is not None
            and initial["status"] == "PARTIAL"
            and all(item["status"] == "CANDIDATE" or _retryable(item) for item in batch_items)
        )
        effective = []
        retry_counts = []
        prior_unresolved = False
        for offset, path in enumerate(paths[index * _BATCH_SIZE : (index + 1) * _BATCH_SIZE]):
            path_index = index * _BATCH_SIZE + offset
            state = result["paths"][path_index]
            item = batch_items[offset] if batch_items is not None else None
            previous = initial
            if item is not None:
                state["status"] = item["status"]
                state["attempts"].append(
                    {
                        "attempt": 0,
                        "status": item["status"],
                        "transport_status": item["transport_status"],
                    }
                )
            gap = False
            observed_retries = 0
            prior_started_ns = None
            for attempt in range(1, 3):
                retry_start_raw = _read(
                    fd, chain, _retry_name(cid, path_index, attempt, "intent"), _RETRY_RESULT_LIMIT
                )
                retry_raw = _read(
                    fd, chain, _retry_name(cid, path_index, attempt, "json"), _RETRY_RESULT_LIMIT
                )
                if retry_start_raw is None and retry_raw is None:
                    gap = True
                    continue
                if (
                    attempt > body["max_retries"]
                    or gap
                    or prior_unresolved
                    or batch_cleanup
                    or not allow_retry
                    or item is None
                    or not _retryable(item)
                ):
                    raise ValueError("invalid retry ordering")
                if retry_start_raw is None:
                    raise ValueError("retry result without intent")
                retry_start, retry_start_sha = _unwrap(retry_start_raw)
                if (
                    set(retry_start)
                    != {
                        "schema",
                        "collection_id",
                        "intent_sha256",
                        "path_index",
                        "attempt",
                        "previous_result_sha256",
                        "previous_finished_ns",
                        "started_ns",
                        "clock_authentication",
                    }
                    or retry_start["schema"] != "bt_api.calendar-detail-retry-intent/2"
                    or retry_start["collection_id"] != cid
                    or retry_start["intent_sha256"] != intent_sha
                    or type(retry_start["path_index"]) is not int
                    or retry_start["path_index"] != path_index
                    or type(retry_start["attempt"]) is not int
                    or retry_start["attempt"] != attempt
                    or retry_start["previous_result_sha256"]
                    != hashlib.sha256(_json(previous)).hexdigest()
                    or type(retry_start["previous_finished_ns"]) is not int
                    or retry_start["previous_finished_ns"] < 0
                    or (
                        prior_started_ns is not None
                        and retry_start["previous_finished_ns"] < prior_started_ns
                    )
                    or type(retry_start["started_ns"]) is not int
                    or retry_start["started_ns"] - retry_start["previous_finished_ns"]
                    < math.ceil(body["retry_interval_seconds"] * 1_000_000_000)
                    or retry_start["clock_authentication"] != "NOT_PROVEN"
                ):
                    raise ValueError("invalid retry intent")
                if retry_raw is None:
                    incomplete = True
                    gap = True
                    state["status"] = "UNKNOWN"
                    state["attempts"].append(
                        {"attempt": attempt, "status": "UNKNOWN", "transport_status": "UNKNOWN"}
                    )
                    if capture is not None and capture["index"] == index:
                        selected_retries.append(
                            {"path_index": path_index, "attempt": attempt, "result": None}
                        )
                    continue
                retry_body, _ = _unwrap(retry_raw)
                if (
                    set(retry_body)
                    != {
                        "schema",
                        "collection_id",
                        "intent_sha256",
                        "path_index",
                        "attempt",
                        "retry_intent_sha256",
                        "result",
                    }
                    or retry_body["schema"] != "bt_api.calendar-detail-retry-result/2"
                    or retry_body["collection_id"] != cid
                    or retry_body["intent_sha256"] != intent_sha
                    or type(retry_body["path_index"]) is not int
                    or retry_body["path_index"] != path_index
                    or type(retry_body["attempt"]) is not int
                    or retry_body["attempt"] != attempt
                    or retry_body["retry_intent_sha256"] != retry_start_sha
                ):
                    raise ValueError("invalid retry result")
                report = detail._validate_state(
                    retry_body["result"],
                    candidate_id=candidate_id,
                    paths=[path],
                    root=root,
                    expected_candidate_payload_sha256=body["candidate_payload_sha256"],
                )
                if report["status"] == "IN_PROGRESS":
                    raise ValueError("unfinished retry")
                if report["status"] == "CLEANUP_UNCERTAIN":
                    batch_cleanup = True
                    cleanup_reason = report["reason"]
                    result.update(cleanup_status="UNCERTAIN", batch_reason=cleanup_reason)
                item = report["items"][0]
                prior_started_ns = retry_start["started_ns"]
                previous = report
                observed_retries += 1
                state["status"] = item["status"]
                state["attempts"].append(
                    {
                        "attempt": attempt,
                        "status": item["status"],
                        "transport_status": item["transport_status"],
                    }
                )
                if capture is not None and capture["index"] == index:
                    selected_retries.append(
                        {"path_index": path_index, "attempt": attempt, "result": report}
                    )
            effective.append(item)
            retry_counts.append(observed_retries)
            if batch_cleanup or (item is not None and item["status"] != "CANDIDATE"):
                prior_unresolved = True
        batches.append(
            {
                "initial": initial,
                "effective": effective,
                "retry_counts": retry_counts,
                "cleanup": batch_cleanup,
            }
            if initial
            else None
        )
    end_raw = _read(fd, chain, f"collection-{cid}.end", _RESULT_LIMIT)
    if end_raw is not None:
        end, _ = _unwrap(end_raw)
        if (
            set(end) != {"schema", "collection_id", "intent_sha256", "started_batches", "reason"}
            or end["schema"] != "bt_api.calendar-detail-collection-end/2"
            or end["collection_id"] != cid
            or end["intent_sha256"] != intent_sha
            or type(end["started_batches"]) is not int
            or not 0 <= end["started_batches"] <= count
            or end["reason"]
            not in {
                "ALL_OBSERVED_UNVERIFIED",
                "BUDGET_EXHAUSTED",
                "BATCH_STOPPED",
                "BATCH_CLEANUP_UNCERTAIN",
                "RETRY_EXHAUSTED",
                "RETRY_BUDGET_EXHAUSTED",
            }
        ):
            raise ValueError("invalid end")
        started = end["started_batches"]
        if (
            incomplete
            or any(x is None for x in batches[:started])
            or any(x is not None for x in batches[started:])
        ):
            raise ValueError("inconsistent end")

        def complete(batch):
            return (
                not batch["cleanup"]
                and batch["initial"]["status"] in {"PARTIAL", "OBSERVED_UNVERIFIED"}
                and all(
                    item is not None and item["status"] == "CANDIDATE"
                    for item in batch["effective"]
                )
            )

        if any(not complete(batch) for batch in batches[: max(0, started - 1)]):
            raise ValueError("invalid batch order")
        reason = end["reason"]
        if reason == "ALL_OBSERVED_UNVERIFIED" and (
            started != count or any(not complete(batch) for batch in batches)
        ):
            raise ValueError("invalid completion")
        if reason == "BUDGET_EXHAUSTED" and (
            started >= count or any(not complete(batch) for batch in batches[:started])
        ):
            raise ValueError("invalid budget stop")
        if reason in {
            "BATCH_STOPPED",
            "BATCH_CLEANUP_UNCERTAIN",
            "RETRY_EXHAUSTED",
            "RETRY_BUDGET_EXHAUSTED",
        } and (started == 0 or complete(batches[started - 1])):
            raise ValueError("invalid stop")
        if reason == "BATCH_CLEANUP_UNCERTAIN" and cleanup_reason is None:
            raise ValueError("missing cleanup")
        if reason != "BATCH_CLEANUP_UNCERTAIN" and cleanup_reason is not None:
            raise ValueError("hidden cleanup")
        if reason in {"RETRY_EXHAUSTED", "RETRY_BUDGET_EXHAUSTED"}:
            batch = batches[started - 1]
            if any(
                item is None or (item["status"] != "CANDIDATE" and not _retryable(item))
                for item in batch["effective"]
            ):
                raise ValueError("invalid retry failure")
            unresolved = [
                (item, n)
                for item, n in zip(batch["effective"], batch["retry_counts"])  # noqa: B905 - Python 3.9
                if item is not None and _retryable(item)
            ]
            if (
                not unresolved
                or (reason == "RETRY_EXHAUSTED" and unresolved[0][1] != body["max_retries"])
                or (reason == "RETRY_BUDGET_EXHAUSTED" and unresolved[0][1] >= body["max_retries"])
            ):
                raise ValueError("invalid retry stop")
        if reason == "BATCH_STOPPED":
            batch = batches[started - 1]
            if all(
                item is not None and (item["status"] == "CANDIDATE" or _retryable(item))
                for item in batch["effective"]
            ):
                raise ValueError("invalid non-retryable stop")
        for item in result["paths"][started * _BATCH_SIZE :]:
            item["status"] = "NOT_ATTEMPTED"
        result["status"] = (
            "OBSERVED_UNVERIFIED"
            if reason == "ALL_OBSERVED_UNVERIFIED"
            else "UNKNOWN"
            if reason == "BATCH_CLEANUP_UNCERTAIN"
            else "PARTIAL"
        )
        result["reason"] = reason
    if cleanup_reason is not None:
        result.update(cleanup_status="UNCERTAIN", batch_reason=cleanup_reason)
    if not _directory_matches(chain) or len(_json(result)) > _OUTPUT_LIMIT:
        raise ValueError("unbounded or changed")
    if capture is not None:
        capture.update(
            report=selected_report,
            retries=selected_retries,
            end_present=end_raw is not None,
            version=2,
        )
    return result


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
        if body["schema"] == "bt_api.calendar-detail-collection-intent/2":
            return _show_v2(fd, chain, body, intent_sha, root, result, capture)
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
    if capture.get("version") == 2:
        result["retry_attempts"] = capture["retries"]
        result["final_paths"] = summary["paths"][
            batch_index * _BATCH_SIZE : (batch_index + 1) * _BATCH_SIZE
        ]
        result["retry"] = "D67_20_TIMEOUT_RETRY"
    if len(_json(result)) > (_RETRY_OUTPUT_LIMIT if capture.get("version") == 2 else _OUTPUT_LIMIT):
        result.update(status="UNKNOWN", reason="OUTPUT_LIMIT", result=None)
        result.pop("retry_attempts", None)
        result.pop("final_paths", None)
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
    run.add_argument("--retry-timeouts", action="store_true")
    run.add_argument("--max-retries", type=int)
    run.add_argument("--retry-interval-seconds", type=float)
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
            if not args.retry_timeouts and (
                args.max_retries is not None or args.retry_interval_seconds is not None
            ):
                raise _ArgumentError

            def emit(value):
                sys.stdout.buffer.write(_json(value) + b"\n")
                sys.stdout.buffer.flush()

            retry_args = {}
            if args.retry_timeouts:
                retry_args = {
                    "retry_timeouts": True,
                    "max_retries": 2 if args.max_retries is None else args.max_retries,
                    "retry_interval_seconds": 600.0
                    if args.retry_interval_seconds is None
                    else args.retry_interval_seconds,
                }
            result = collect_once(
                root=args.calendar_root,
                run_id=args.run_id,
                filesystem=filesystem,
                budget_seconds=args.budget_seconds,
                per_request_seconds=args.per_request_seconds,
                collection_id=collection_id,
                emit=emit,
                **retry_args,
            )
        output = _json(result)
        if len(output) > (
            _RETRY_OUTPUT_LIMIT
            if args.command == "show-batch" and "retry_attempts" in result
            else _OUTPUT_LIMIT
        ):
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
