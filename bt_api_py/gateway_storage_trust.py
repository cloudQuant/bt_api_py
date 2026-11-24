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


# --- Windows parity -------------------------------------------------------
# These helpers are only reached on Windows (no ``os.geteuid``).  They mirror
# the POSIX checks above with Win32 equivalents; every refusal is a
# category-only code, so no path, SID or account value is ever exposed.
# The POSIX helpers below are deliberately left untouched.

# Bits that let a principal mutate data / ownership.  GENERIC_ALL is included
# because a stored DACL may keep it unexpanded.  This mirrors the write set
# used by ``bt_api_py._win32_security`` (with GENERIC_ALL added for safety).
_WINDOWS_WRITE_BITS = (
    0x40000000  # GENERIC_WRITE
    | 0x10000000  # GENERIC_ALL
    | 0x0002  # FILE_WRITE_DATA
    | 0x0004  # FILE_APPEND_DATA
    | 0x0040  # FILE_DELETE_CHILD
    | 0x00010000  # DELETE
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
)
_WINDOWS_PRIVILEGED_SIDS = frozenset({
    "S-1-5-18",      # SYSTEM (POSIX root analogue)
    "S-1-5-32-544",  # Administrators
})


def _win32_require_primitives() -> object:
    from bt_api_py import _win32_security as w32

    if not w32.has_privileges_available():
        _fail("STORAGE_TRUST_UNSUPPORTED_PLATFORM")
    return w32


def _win32_check_reparse(path: Path, *, ancestor: bool) -> None:
    """Reject a symlink / junction / mount point on the path chain."""
    from bt_api_py._win32_security import Win32SecurityUnavailable, is_reparse_point

    code = "STORAGE_ANCESTOR_UNTRUSTED" if ancestor else "STORAGE_SYMLINK"
    try:
        reparse = is_reparse_point(path)
    except Win32SecurityUnavailable:
        _fail(code)
    if reparse:
        _fail(code)


def _win32_others_writable(path: Path, current_sid: str) -> bool:
    """True if a non-owner, non-privileged SID holds a write ACE.

    ``inspect_owner`` from the foundation module currently reads the ACE
    *flags* as the access mask (``ace[0]`` instead of ``ace[1]``), which can
    both miss a real write ACE and flag an inheritable read-only one.  Read
    the DACL here with the correct tuple element so the gate is not relaxed;
    owner identity still comes from ``inspect_owner``.
    """
    try:
        import win32security

        flags = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
        dacl = win32security.GetFileSecurity(os.fspath(path), flags).GetSecurityDescriptorDacl()
        if dacl is None:  # NULL DACL grants everyone full access
            return True
        for index in range(dacl.GetAceCount()):
            (ace_type, _ace_flags), mask, sid = dacl.GetAce(index)
            if ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
                continue
            if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE:
                return True
            if not mask & _WINDOWS_WRITE_BITS:
                continue
            sid_str = win32security.ConvertSidToStringSid(sid)
            if sid_str == current_sid or sid_str in _WINDOWS_PRIVILEGED_SIDS:
                continue
            return True
        return False
    except Exception:
        return True  # fail closed


def _win32_check_owned(path: Path, current_sid: str, *, read_code: str) -> None:
    from bt_api_py._win32_security import Win32SecurityUnavailable, inspect_owner

    try:
        info = inspect_owner(path, others_may_write_check=False)
    except Win32SecurityUnavailable:
        _fail(read_code)
    if not info.is_current_user or info.owner_sid != current_sid:
        _fail("STORAGE_OWNER_MISMATCH")
    if _win32_others_writable(path, current_sid):
        _fail("STORAGE_WRITABLE_BY_OTHERS")


def _assert_gateway_storage_isolated_windows(
    directories: Iterable[str | os.PathLike[str]],
    files: Iterable[str | os.PathLike[str]],
    euid: int | None,
) -> None:
    w32 = _win32_require_primitives()
    if euid is not None:
        # A Windows SID cannot be expressed as the POSIX integer euid.
        _fail("STORAGE_TRUST_INVALID")
    try:
        current_sid = w32.current_user_sid()
    except w32.Win32SecurityUnavailable:
        _fail("STORAGE_TRUST_UNSUPPORTED_PLATFORM")

    checked: set[str] = set()
    for raw in directories:
        directory = Path(raw)
        if not directory.is_absolute():
            _fail("STORAGE_PATH_INVALID")
        key = os.path.normcase(str(directory))
        if key in checked:
            continue
        checked.add(key)
        # Reparse refusal at every level of both the literal and the canonical
        # chain, mirroring ``_check_chain``.  Ancestor owner/DACL checks are
        # intentionally not attempted: Win32 system roots (C:\\, C:\\Users)
        # expose write ACEs to broad principals with no sticky-bit analogue,
        # so they are not a usable ownership signal (documented non-equivalence).
        for chain in (directory, Path(os.path.realpath(directory))):
            for ancestor in reversed(chain.parents):
                _win32_check_reparse(ancestor, ancestor=True)
        if not os.path.lexists(directory):
            _fail("STORAGE_DIRECTORY_MISSING")
        _win32_check_reparse(directory, ancestor=False)
        if not directory.is_dir():
            _fail("STORAGE_NOT_DIRECTORY")
        _win32_check_owned(directory, current_sid, read_code="STORAGE_DIRECTORY_UNREADABLE")
        try:
            entries = list(os.scandir(directory))
        except OSError:
            _fail("STORAGE_DIRECTORY_UNREADABLE")
        for entry in entries:
            path = Path(entry.path)
            _win32_check_reparse(path, ancestor=False)
            _win32_check_owned(path, current_sid, read_code="STORAGE_ENTRY_UNREADABLE")

    for raw in files:
        path = Path(raw)
        if not path.is_absolute():
            _fail("STORAGE_PATH_INVALID")
        for candidate in (path, *(Path(str(path) + side) for side in _SQLITE_SIDE_FILES)):
            try:
                exists = os.path.lexists(candidate)
            except OSError:
                _fail("STORAGE_ENTRY_UNREADABLE")
            if not exists:
                continue
            _win32_check_reparse(candidate, ancestor=False)
            if not candidate.is_file():
                _fail("STORAGE_NOT_REGULAR_FILE")
            _win32_check_owned(candidate, current_sid, read_code="STORAGE_ENTRY_UNREADABLE")


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
    if os.name == "nt" and not hasattr(os, "geteuid"):
        # Windows parity: reparse refusal on the whole chain, owner == current
        # SID and no other-principal write ACE.  The POSIX path below is
        # deliberately unchanged.
        return _assert_gateway_storage_isolated_windows(directories, files, euid)
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
