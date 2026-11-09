"""OS-enforced storage isolation check for a Gateway (iteration 63).

Trust model (user decision, 2026-11-09): the Gateway runs as its own OS user,
its journal and state directories are writable only by that user, and strategy
processes run as other users. The operating system enforces the isolation;
SQLite path or anchor checks alone cannot prove which file a connection opened.

This check refuses startup when the on-disk permission state would let another
user replace or modify Gateway storage: a storage directory, any entry in it or
an ancestor directory that another user could write or redirect. It cannot
detect a second process running as the Gateway's own user, nor a privileged
(root) actor; both remain deployment requirements, documented for operators.
Error codes are categories only and never include paths or account data.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path

_SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")


class GatewayStorageTrustError(RuntimeError):
    """Startup refused: the storage permission state does not isolate the Gateway."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise GatewayStorageTrustError(code)


def _check_ancestor(path: Path, euid: int) -> None:
    try:
        st = os.lstat(path)
    except OSError:
        _fail("STORAGE_ANCESTOR_UNREADABLE")
    if stat.S_ISLNK(st.st_mode):
        # Its containing directory was already checked; follow and check the target chain.
        return
    if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, euid):
        _fail("STORAGE_ANCESTOR_UNTRUSTED")
    # A directory others may write is acceptable only with the sticky bit:
    # then nobody else can rename or remove the Gateway's own entries.
    if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
        _fail("STORAGE_ANCESTOR_WRITABLE_BY_OTHERS")


def _check_chain(directory: Path, euid: int) -> None:
    """Every ancestor of the configured path and of its real path."""
    for chain in (directory, Path(os.path.realpath(directory))):
        for ancestor in reversed(chain.parents):
            _check_ancestor(ancestor, euid)


def _check_owned(st: os.stat_result, euid: int) -> None:
    if stat.S_ISLNK(st.st_mode):
        _fail("STORAGE_SYMLINK")
    if st.st_uid != euid:
        _fail("STORAGE_OWNER_MISMATCH")
    if st.st_mode & 0o022:
        _fail("STORAGE_WRITABLE_BY_OTHERS")


def assert_gateway_storage_isolated(
    directories: Iterable[str | os.PathLike[str]],
    files: Iterable[str | os.PathLike[str]] = (),
    *,
    euid: int | None = None,
) -> None:
    """Refuse unless every storage directory and file is isolated to ``euid``.

    ``directories`` must already exist. ``files`` (SQLite databases) may not
    exist yet; when they do, they and their WAL/SHM/journal side files are
    checked as well (their directory is expected among ``directories``).
    """
    if not hasattr(os, "geteuid") or not hasattr(os, "lstat"):
        _fail("STORAGE_TRUST_UNSUPPORTED_PLATFORM")
    if euid is None:
        euid = os.geteuid()
    if type(euid) is not int or euid < 0:
        _fail("STORAGE_TRUST_INVALID")
    checked: set[Path] = set()
    for raw in directories:
        directory = Path(raw)
        if not directory.is_absolute():
            _fail("STORAGE_PATH_INVALID")
        if directory in checked:
            continue
        checked.add(directory)
        _check_chain(directory, euid)
        try:
            st = os.lstat(directory)
        except OSError:
            _fail("STORAGE_DIRECTORY_MISSING")
        if not stat.S_ISDIR(st.st_mode):
            _fail("STORAGE_NOT_DIRECTORY")
        _check_owned(st, euid)
        try:
            entries = list(os.scandir(directory))
        except OSError:
            _fail("STORAGE_DIRECTORY_UNREADABLE")
        for entry in entries:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError:
                _fail("STORAGE_ENTRY_UNREADABLE")
            _check_owned(entry_stat, euid)
    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            _fail("STORAGE_PATH_INVALID")
        for candidate in (path, *(Path(str(path) + side) for side in _SQLITE_SIDE_FILES)):
            try:
                st = os.lstat(candidate)
            except FileNotFoundError:
                continue
            except OSError:
                _fail("STORAGE_ENTRY_UNREADABLE")
            if not stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                _fail("STORAGE_NOT_REGULAR_FILE")
            _check_owned(st, euid)
