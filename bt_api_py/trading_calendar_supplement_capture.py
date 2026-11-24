"""Immutable local captures of one guarded, unverified supplement read.

The content digest is over CTP-normalized JSON, not raw YAML. Capture and run
records are separate local writes: neither authenticates association, source,
writer, or persistence across power loss. Only trusted cooperating POSIX
writers are supported; metadata rechecks cannot exclude ABA or hostile races.
``read_capture`` is an internal public-input API and returns full content only
when explicitly requested. No network, accounts, CURRENT or publication.
"""

import contextlib
from bt_api_py._calendar_compat import fcntl
import hashlib
import json
import os
from bt_api_py import _calendar_fdops as fdops
import stat
import uuid

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _remove_owned_temp,
    _supported,
)

_SCHEMA = "bt_api.calendar-supplement-capture/1"
_LIMIT = 2 * 1024 * 1024


def _open(root, create):
    fds, chain = _open_directory(root)
    try:
        parent = fds[-1]
        if create:
            with contextlib.suppress(FileExistsError):
                fdops.mkdir("supplement-captures", 0o700, dir_fd=parent)
        info = fdops.stat("supplement-captures", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != fdops.geteuid():
            raise ValueError("invalid directory")
        fd = fdops.open(
            "supplement-captures", os.O_RDONLY | fdops.O_DIRECTORY | fdops.O_NOFOLLOW, dir_fd=parent
        )
        fds.append(fd)
        if _identity(fdops.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "supplement-captures", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _normalize(candidate):
    from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate

    result = normalize_calendar_candidate(candidate)
    if (
        result.status != "NORMALIZED"
        or type(result.json_bytes) is not bytes
        or len(result.json_bytes) > _LIMIT
    ):
        raise ValueError("invalid supplement")
    if hashlib.sha256(result.json_bytes).hexdigest() != result.sha256:
        raise ValueError("digest mismatch")
    content = json.loads(result.json_bytes, object_pairs_hook=runs._unique_pairs)
    return content, result.sha256


def retain_capture(*, root, run_id, capture_id, candidate, guard, filesystem, sensitive_paths=()):
    """Install once from a captured candidate; never read its input path again."""
    if (
        not detail._safe_root(root)
        or not runs._id(run_id)
        or not runs._id(capture_id)
        or capture_id == run_id
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or not callable(guard)
        or type(sensitive_paths) is not tuple
        or any(type(p) is not str or not p for p in sensitive_paths)
    ):
        return {
            "status": "NOT_COMMITTED",
            "capture_id": capture_id if runs._id(capture_id) else "",
            "payload_sha256": None,
        }
    try:
        content, digest = _normalize(candidate)
        if detail._sensitive(content, root) or any(
            detail._sensitive(content, path) for path in sensitive_paths
        ):
            raise ValueError("sensitive content")
        body = {
            "schema": _SCHEMA,
            "run_id": run_id,
            "capture_id": capture_id,
            "content": content,
            "normalized_content_sha256": digest,
            "verification": "UNVERIFIED",
            "source_authentication": "NOT_PROVEN",
            "association": "NOT_PROVEN",
            "writer_identity": "NOT_PROVEN",
            "durability": "NOT_PROVEN",
            "publication": "NOT_AUTHORIZED",
        }
        encoded = storage._envelope(body)
        if len(encoded) > _LIMIT or guard() is not True:
            raise ValueError("changed input")
        fds, chain = _open(root, True)
    except (Exception, KeyboardInterrupt):
        return {"status": "NOT_COMMITTED", "capture_id": capture_id, "payload_sha256": None}
    lock = None
    fd = None
    temp = ""
    identity = None
    attempted = False
    try:
        lock, lock_id = storage._lock(fds[-1], chain)
        if guard() is not True or _normalize(candidate) != (content, digest):
            return {"status": "NOT_COMMITTED", "capture_id": capture_id, "payload_sha256": None}
        temp = f".capture-{uuid.uuid4().hex}.tmp"
        fd = fdops.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | fdops.O_NOFOLLOW, 0o600, dir_fd=fds[-1]
        )
        fdops.fchmod(fd, 0o600)
        identity = _identity(fdops.fstat(fd))
        offset = 0
        while offset < len(encoded):
            count = fdops.write(fd, encoded[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        fdops.fsync(fd)
        storage._check_lock(fds[-1], chain, lock, lock_id)
        if (
            guard() is not True
            or _normalize(candidate) != (content, digest)
            or _identity(fdops.fstat(fd)) != identity
            or _identity(fdops.stat(temp, dir_fd=fds[-1], follow_symlinks=False)) != identity
        ):
            return {"status": "NOT_COMMITTED", "capture_id": capture_id, "payload_sha256": None}
        name = f"capture-{capture_id}.json"
        attempted = True
        try:
            fdops.link(temp, name, src_dir_fd=fds[-1], dst_dir_fd=fds[-1], follow_symlinks=False)
        except FileExistsError:
            old = fdops.stat(name, dir_fd=fds[-1], follow_symlinks=False)
            if _identity(old) == identity or not _directory_matches(chain):
                raise
            attempted = False
            return {"status": "NOT_COMMITTED", "capture_id": capture_id, "payload_sha256": None}
        if (
            storage._read(fds[-1], chain, name, _LIMIT) != encoded
            or _identity(fdops.stat(name, dir_fd=fds[-1], follow_symlinks=False)) != identity
            or _identity(fdops.fstat(fd)) != identity
            or _identity(fdops.stat(temp, dir_fd=fds[-1], follow_symlinks=False)) != identity
        ):
            raise ValueError("capture changed")
        _remove_owned_temp(fds[-1], temp, identity)
        try:
            fdops.stat(temp, dir_fd=fds[-1], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("temporary entry changed or remained")
        identity = None
        fdops.fsync(fds[-1])
        storage._check_lock(fds[-1], chain, lock, lock_id)
        # A change observed after installation makes the outcome uncertain. This
        # check cannot exclude non-cooperating writers, ABA, or later changes.
        if guard() is not True or _normalize(candidate) != (content, digest):
            raise ValueError("input changed after installation")
        return {
            "status": "COMMITTED",
            "capture_id": capture_id,
            "payload_sha256": hashlib.sha256(storage._json(body)).hexdigest(),
        }
    except (Exception, KeyboardInterrupt):
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            "capture_id": capture_id,
            "payload_sha256": None,
        }
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                fdops.close(fd)
        if identity is not None:
            with contextlib.suppress(OSError):
                _remove_owned_temp(fds[-1], temp, identity)
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                fdops.close(lock)
        runs._close(fds)


def read_capture(*, root, capture_id, filesystem, include_content=False):
    """Verify a retained envelope; full unredacted content is explicit opt-in."""
    unknown = {"status": "UNKNOWN", "capture_id": capture_id if runs._id(capture_id) else ""}
    if (
        not detail._safe_root(root)
        or not runs._id(capture_id)
        or filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or type(include_content) is not bool
    ):
        return unknown
    try:
        fds, chain = _open(root, False)
        try:
            raw = storage._read(fds[-1], chain, f"capture-{capture_id}.json", _LIMIT)
            if raw is None:
                return {"status": "NOT_OBSERVED", "capture_id": capture_id}
            body, digest = storage._unwrap(raw)
            if (
                set(body)
                != {
                    "schema",
                    "run_id",
                    "capture_id",
                    "content",
                    "normalized_content_sha256",
                    "verification",
                    "source_authentication",
                    "association",
                    "writer_identity",
                    "durability",
                    "publication",
                }
                or body["schema"] != _SCHEMA
                or body["capture_id"] != capture_id
                or not runs._id(body["run_id"])
                or body["run_id"] == capture_id
                or (
                    body["verification"],
                    body["source_authentication"],
                    body["association"],
                    body["writer_identity"],
                    body["durability"],
                    body["publication"],
                )
                != (
                    "UNVERIFIED",
                    "NOT_PROVEN",
                    "NOT_PROVEN",
                    "NOT_PROVEN",
                    "NOT_PROVEN",
                    "NOT_AUTHORIZED",
                )
            ):
                raise ValueError("invalid capture")
            from bt_api_ctp.calendar_supplement import _validate

            content, normalized_digest = _normalize(_validate(body["content"]))
            if (
                content != body["content"]
                or normalized_digest != body["normalized_content_sha256"]
                or detail._sensitive(content, root)
            ):
                raise ValueError("invalid content")
            result = {
                "status": "VISIBLE_UNAUTHENTICATED",
                "capture_id": capture_id,
                "run_id": body["run_id"],
                "payload_sha256": digest,
                "normalized_content_sha256": normalized_digest,
                "association": "NOT_PROVEN",
                "writer_identity": "NOT_PROVEN",
                "source_authentication": "NOT_PROVEN",
                "durability": "NOT_PROVEN",
                "publication": "NOT_AUTHORIZED",
            }
            if include_content:
                result["content"] = content
            return result
        finally:
            runs._close(fds)
    except Exception:
        return unknown
