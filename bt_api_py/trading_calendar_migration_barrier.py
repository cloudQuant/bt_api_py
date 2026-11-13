"""Fail-closed migration fence for Store writers holding the Store lock.

This checks a cooperating local POSIX directory, not hostile ABA changes or an
older writer which ignores the lock/fence. No lock is acquired here.
"""

import hashlib
import json
import os
import re
import stat


class MigrationBarrierError(ValueError):
    """The migration fence is present or cannot be safely assessed."""


def _identity(info):
    return info.st_dev, info.st_ino


def _fingerprint(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationBarrierError("duplicate migration head field")
        result[key] = value
    return result


def _head_ok(raw):
    envelope = json.loads(raw, object_pairs_hook=_pairs)
    if type(envelope) is not dict or set(envelope) != {"body", "sha256"}:
        return False
    body = envelope["body"]
    if type(body) is not dict:
        return False
    encoded = json.dumps(
        body, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    if envelope["sha256"] != hashlib.sha256(encoded).hexdigest():
        return False

    def hex32(value):
        return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None

    def hex64(value):
        return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    if body.get("schema") == "bt_api.calendar-scope-check-head/1":
        return (
            set(body) == {"schema", "sequence", "record_sha256", "batch_id"}
            and type(body["sequence"]) is int
            and body["sequence"] > 0
            and hex64(body["record_sha256"])
            and hex32(body["batch_id"])
        )
    return (
        set(body)
        == {
            "schema",
            "state",
            "migration_id",
            "intent_sha256",
            "environment",
            "old_writers",
            "database_id",
        }
        and body.get("schema") == "bt_api.calendar-scope-check-head/2"
        and body["state"] == "READY"
        and hex32(body["migration_id"])
        and hex64(body["intent_sha256"])
        and hex32(body["database_id"])
        and body["environment"] == "SIMULATION_ONLY"
        and body["old_writers"] == "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED"
    )


def check_migration_barrier(root, lock_fd):
    """Reject a present/uncertain fence, anchored to the currently held lock."""
    root_fd = runs_fd = head_fd = None
    try:
        if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
            raise MigrationBarrierError("safe directory checks unavailable")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(root, flags)
        root_info = os.fstat(root_fd)
        lock_info = os.fstat(lock_fd)

        def check_root():
            path_info = os.stat(root, follow_symlinks=False)
            lock_entry = os.stat(".lock", dir_fd=root_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(path_info.st_mode)
                or _identity(path_info) != _identity(root_info)
                or not stat.S_ISREG(lock_entry.st_mode)
                or _identity(lock_entry) != _identity(lock_info)
            ):
                raise MigrationBarrierError("store root or lock changed")

        def check_runs():
            entry = os.stat("observation-runs", dir_fd=root_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(entry.st_mode)
                or _identity(entry) != _identity(runs_info)
                or _identity(os.fstat(runs_fd)) != _identity(runs_info)
            ):
                raise MigrationBarrierError("observation directory changed")
            check_root()

        check_root()
        try:
            runs_info = os.stat("observation-runs", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            check_root()
            try:
                os.stat("observation-runs", dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                check_root()
                return
            raise MigrationBarrierError("observation directory appeared") from None
        if (
            not stat.S_ISDIR(runs_info.st_mode)
            or runs_info.st_mode & 0o077
            or runs_info.st_uid != os.geteuid()
        ):
            raise MigrationBarrierError("invalid observation directory")
        runs_fd = os.open("observation-runs", flags, dir_fd=root_fd)
        check_runs()
        head_fingerprint = None
        for name in ("scope-check-migration-pending.json", "scope-check-head.json"):
            try:
                entry = os.stat(name, dir_fd=runs_fd, follow_symlinks=False)
            except FileNotFoundError:
                check_runs()
                try:
                    os.stat(name, dir_fd=runs_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise MigrationBarrierError("migration entry appeared") from None
            if name == "scope-check-migration-pending.json":
                raise MigrationBarrierError("scope migration pending")
            if (
                not stat.S_ISREG(entry.st_mode)
                or entry.st_mode & 0o077
                or entry.st_uid != os.geteuid()
                or entry.st_nlink != 1
                or entry.st_size > 65536
            ):
                raise MigrationBarrierError("invalid migration head")
            head_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=runs_fd)
            before = os.fstat(head_fd)
            data = bytearray()
            while len(data) <= 65536:
                part = os.read(head_fd, 65537 - len(data))
                if not part:
                    break
                data.extend(part)
            after = os.fstat(head_fd)
            current = os.stat(name, dir_fd=runs_fd, follow_symlinks=False)
            if (
                _fingerprint(before) != _fingerprint(entry)
                or _fingerprint(after) != _fingerprint(before)
                or _fingerprint(current) != _fingerprint(before)
                or len(data) != before.st_size
                or not _head_ok(bytes(data))
            ):
                raise MigrationBarrierError("migration head unavailable or invalid")
            head_fingerprint = _fingerprint(before)
        check_runs()
        # Check the singleton again after reading the head.
        try:
            os.stat("scope-check-migration-pending.json", dir_fd=runs_fd, follow_symlinks=False)
        except FileNotFoundError:
            check_runs()
        else:
            raise MigrationBarrierError("scope migration pending")
        try:
            final_head = os.stat("scope-check-head.json", dir_fd=runs_fd, follow_symlinks=False)
        except FileNotFoundError:
            if head_fingerprint is not None:
                raise MigrationBarrierError("migration head disappeared") from None
        else:
            if (
                head_fingerprint is None
                or _fingerprint(final_head) != head_fingerprint
                or _fingerprint(os.fstat(head_fd)) != head_fingerprint
            ):
                raise MigrationBarrierError("migration head changed")
        check_runs()
    finally:
        for fd in (head_fd, runs_fd, root_fd):
            if fd is not None:
                os.close(fd)
