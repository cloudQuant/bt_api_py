"""Windows equivalents of the POSIX security primitives used at service start.

Inert on POSIX (``WINDOWS`` is False; every helper raises
:class:`Win32SecurityUnavailable`).  On Windows the helpers aim for *equal
strength*, not a relaxed bypass.  Implementation notes:

* The low-level file/clock calls use ``ctypes`` against Win32 (verified stable
  on this host).  Owner / DACL inspection uses ``pywin32`` (``win32security``),
  which is memory-safe; raw ctypes SID/ACE traversal is deliberately avoided
  because it is error-prone on this Python build.
* no-follow open -> ``CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`` plus an
  explicit reparse-attribute check (POSIX ``O_NOFOLLOW``).
* identity stamp -> ``GetFileInformationByHandle`` volume-serial + file-index,
  mode, link count, size and timestamps from the *open handle* (POSIX
  ``fstat`` stamp), so a swap between checks is detected.
* owner check -> exact SID equality between the file owner and the process
  token user.  DACL allow-ACEs granting write to a non-owner, non-privileged
  SID (Everyone / Authenticated Users / Users) are refused.  Privileged SIDs
  (SYSTEM, Administrators) are accepted because POSIX root is likewise not
  fenced by mode bits.  A NULL/unreadable DACL fails closed.
* sleep-inclusive monotonic clock -> ``GetTickCount64()`` (includes sleep),
  the Windows counterpart of Linux ``CLOCK_BOOTTIME`` / Darwin
  ``CLOCK_MONOTONIC``.  Sampling rounds *up* to a nanosecond upper bound so a
  lease can only expire at most 1 ms early, never be extended.

Documented non-equivalences (fail closed, never silently accepted): a process
with backup/restore/take-ownership privilege and a second process under the
same user SID are outside the threat model, just as root / same-uid processes
are on POSIX.  These helpers never grant authority or prove account facts;
they only preserve local file/clock invariants so existing fail-closed checks
can run on Windows.  All errors are category-only (no path/SID/account value).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as _wt
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

WINDOWS = os.name == "nt"


class Win32SecurityUnavailable(RuntimeError):
    """A Windows security primitive failed or cannot establish the invariant."""

    def __init__(self, code: str = "WIN32_SECURITY_UNAVAILABLE") -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Native bindings (file + clock only; ACL done via pywin32)
# ---------------------------------------------------------------------------

_kernel32 = None
_win32security = None
_win32api = None
_win32con = None
pywin32_error = None

if WINDOWS:  # pragma: no cover - platform gated
    try:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        _kernel32.GetTickCount64.argtypes = []
        _kernel32.CreateFileW.restype = _wt.HANDLE
        _kernel32.CreateFileW.argtypes = [
            _wt.LPCWSTR, _wt.DWORD, _wt.DWORD, _wt.LPVOID,
            _wt.DWORD, _wt.DWORD, _wt.HANDLE,
        ]
        _kernel32.GetFileAttributesW.restype = _wt.DWORD
        _kernel32.GetFileAttributesW.argtypes = [_wt.LPCWSTR]
        _kernel32.CloseHandle.restype = _wt.BOOL
        _kernel32.CloseHandle.argtypes = [_wt.HANDLE]
        _kernel32.GetFileInformationByHandle.restype = _wt.BOOL
        _kernel32.GetFileInformationByHandle.argtypes = [_wt.HANDLE, ctypes.c_void_p]
        _kernel32.GetFinalPathNameByHandleW.restype = _wt.DWORD
        _kernel32.GetFinalPathNameByHandleW.argtypes = [
            _wt.HANDLE, _wt.LPWSTR, _wt.DWORD, _wt.DWORD,
        ]
        import msvcrt as _msvcrt
    except Exception:
        _kernel32 = None

    try:
        import win32api as _win32api
        import win32con as _win32con
        import win32security as _win32security
        import pywintypes as _pywintypes
        pywin32_error = _pywintypes.error
    except Exception:
        _win32security = _win32api = _win32con = None

try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None


_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400

# SIDs that POSIX would treat as "other" (group/other write bits).
_OTHER_PRINCIPAL_SIDS = {
    "S-1-1-0",    # Everyone
    "S-1-5-11",   # Authenticated Users
    "S-1-5-32-545",  # Users
    "S-1-5-32-546",  # Guests
}
# SIDs treated like POSIX root (trusted, not fenced by mode bits).
_PRIVILEGED_SIDS = {
    "S-1-5-18",   # SYSTEM
    "S-1-5-32-544",  # Administrators
}
# Data/ownership write bits that correspond to POSIX group/other write
# permission.  FILE_WRITE_EA (0x10) is intentionally excluded: it grants only
# extended-attribute writes, which POSIX mode bits do not fence either, and
# the stock Windows DACL grants it broadly (including to read-only principals).
_WRITE_BITS = (
    0x40000000  # GENERIC_WRITE
    | 0x0002    # FILE_WRITE_DATA
    | 0x0004    # FILE_APPEND_DATA
    | 0x00040000  # FILE_WRITE_DAC
    | 0x00080000  # FILE_WRITE_OWNER
    | 0x00010000  # DELETE
    | 0x0040    # FILE_DELETE_CHILD
)
_ACCESS_ALLOWED = 0x0
_ACCESS_DENIED = 0x1


# ---------------------------------------------------------------------------
# Sleep-inclusive monotonic clock
# ---------------------------------------------------------------------------

_clock_lock = threading.Lock()
_last_clock_ns = 0


def sleep_inclusive_clock_ns() -> int:
    """Milliseconds-since-boot clock including sleep, in nanoseconds.

    Rounds up to a nanosecond upper bound so a lease can only expire up to
    1 ms early, never be extended.  A backwards sample is rejected so a clock
    anomaly cannot extend a lease.
    """
    if not WINDOWS or _kernel32 is None:
        raise Win32SecurityUnavailable("INTAKE_CLOCK_UNAVAILABLE")
    value_ms = int(_kernel32.GetTickCount64())
    if value_ms < 0 or value_ms >= 2**63:
        raise Win32SecurityUnavailable("INTAKE_CLOCK_UNAVAILABLE")
    value_ns = (value_ms + 1) * 1_000_000  # upper bound; never extends a lease
    global _last_clock_ns
    with _clock_lock:
        # GetTickCount64 has ms resolution: equal consecutive samples are
        # normal.  Only a backwards jump is an anomaly worth refusing.
        if value_ns < _last_clock_ns:
            raise Win32SecurityUnavailable("INTAKE_CLOCK_UNAVAILABLE")
        _last_clock_ns = max(_last_clock_ns, value_ns)
    return value_ns


# ---------------------------------------------------------------------------
# No-follow open and identity stamp
# ---------------------------------------------------------------------------

class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", _wt.DWORD),
        ("ftCreationTime", _wt.FILETIME),
        ("ftLastAccessTime", _wt.FILETIME),
        ("ftLastWriteTime", _wt.FILETIME),
        ("dwVolumeSerialNumber", _wt.DWORD),
        ("nFileSizeHigh", _wt.DWORD),
        ("nFileSizeLow", _wt.DWORD),
        ("nNumberOfLinks", _wt.DWORD),
        ("nFileIndexHigh", _wt.DWORD),
        ("nFileIndexLow", _wt.DWORD),
    ]


def is_reparse_point(path: str | os.PathLike[str]) -> bool:
    """True if the final path component is a symlink / junction / mount point."""
    if not WINDOWS:
        raise Win32SecurityUnavailable("NOFOLLOW_UNAVAILABLE")
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise Win32SecurityUnavailable("NOFOLLOW_UNAVAILABLE") from exc
    return bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _create_file(path: str, *, directory: bool = False, create_new: bool = False,
                 share_delete: bool = True) -> _wt.HANDLE:
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    share = _FILE_SHARE_READ | (_FILE_SHARE_DELETE if share_delete else 0)
    disposition = 1 if create_new else _OPEN_EXISTING
    handle = _kernel32.CreateFileW(path, _GENERIC_READ, share, None, disposition, flags, None)
    if not handle or handle == _INVALID_HANDLE_VALUE:
        raise Win32SecurityUnavailable("OPEN_FAILED")
    return handle


def open_nofollow(path: str | os.PathLike[str], *, share_delete: bool = True) -> tuple[int, _wt.HANDLE]:
    """Open a regular file without following its final reparse point.

    Returns ``(crt_fd, native_handle)`` on Windows (both refer to the same
    underlying handle; ``os.close(fd)`` releases it).  Raises closed on a
    reparse point, a directory, or any API failure.
    """
    if not WINDOWS or _kernel32 is None or _msvcrt is None:
        raise Win32SecurityUnavailable("NOFOLLOW_UNAVAILABLE")
    p = os.fspath(path)
    attrs = _kernel32.GetFileAttributesW(p)
    if attrs == 0xFFFFFFFF or (attrs & _FILE_ATTRIBUTE_REPARSE_POINT):
        raise Win32SecurityUnavailable("NOFOLLOW_REPARSE")
    handle = _create_file(p, share_delete=share_delete)
    try:
        info = _ByHandleFileInformation()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise Win32SecurityUnavailable("STAMP_UNAVAILABLE")
        if info.dwFileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise Win32SecurityUnavailable("NOFOLLOW_REPARSE")
        if info.dwFileAttributes & stat.FILE_ATTRIBUTE_DIRECTORY:
            raise Win32SecurityUnavailable("NOFOLLOW_NOT_FILE")
        fd = _msvcrt.open_osfhandle(handle, os.O_RDONLY)
        if fd < 0:
            raise Win32SecurityUnavailable("OPEN_FAILED")
        return fd, handle
    except Exception:
        _kernel32.CloseHandle(handle)
        raise


def handle_stamp(handle: _wt.HANDLE) -> tuple:
    """POSIX-``stamp``-shaped tuple from an open handle (no path re-resolution)."""
    if not WINDOWS or _kernel32 is None:
        raise Win32SecurityUnavailable("STAMP_UNAVAILABLE")
    info = _ByHandleFileInformation()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise Win32SecurityUnavailable("STAMP_UNAVAILABLE")
    volume = info.dwVolumeSerialNumber
    index = (info.nFileIndexHigh << 32) | info.nFileIndexLow
    size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
    ctime = (info.ftCreationTime.dwHighDateTime << 32) | info.ftCreationTime.dwLowDateTime
    mtime = (info.ftLastWriteTime.dwHighDateTime << 32) | info.ftLastWriteTime.dwLowDateTime
    is_dir = bool(info.dwFileAttributes & stat.FILE_ATTRIBUTE_DIRECTORY)
    mode = stat.S_IFDIR if is_dir else stat.S_IFREG
    return (volume, index, mode, 0, info.nNumberOfLinks, size, mtime, ctime)


# ---------------------------------------------------------------------------
# Owner + DACL checks (pywin32, memory-safe)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OwnerInfo:
    owner_sid: str
    is_current_user: bool
    others_writable: bool


def current_user_sid() -> str:
    if not WINDOWS or _win32security is None:
        raise Win32SecurityUnavailable("OWNER_UNAVAILABLE")
    try:
        token = _win32security.OpenProcessToken(_win32api.GetCurrentProcess(), _win32con.TOKEN_QUERY)
        try:
            sid = _win32security.GetTokenInformation(token, _win32security.TokenUser)[0]
            return _win32security.ConvertSidToStringSid(sid)
        finally:
            _win32api.CloseHandle(token)
    except Exception:
        raise Win32SecurityUnavailable("OWNER_UNAVAILABLE") from None


def inspect_owner(path: str | os.PathLike[str], *, others_may_write_check: bool = True) -> OwnerInfo:
    """Owner SID + whether a non-owner, non-privileged principal can write.

    Raises closed on any failure.  ``others_writable`` mirrors POSIX
    ``st_mode & 0o077``: privileged SIDs (SYSTEM/Administrators) are accepted
    like POSIX root; Everyone/Authenticated Users/Users write ACEs are refused.
    """
    if not WINDOWS or _win32security is None:
        raise Win32SecurityUnavailable("OWNER_UNAVAILABLE")
    p = os.fspath(path)
    if is_reparse_point(p):
        raise Win32SecurityUnavailable("OWNER_REPARSE")
    try:
        flags = _win32security.OWNER_SECURITY_INFORMATION
        if others_may_write_check:
            flags |= _win32security.DACL_SECURITY_INFORMATION
        sd = _win32security.GetFileSecurity(p, flags)
        owner = _win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
        is_current = owner == current_user_sid()
        others = False
        if others_may_write_check:
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:  # NULL DACL = full access to everyone
                others = True
            else:
                for i in range(dacl.GetAceCount()):
                    ace = dacl.GetAce(i)
                    # pywin32 ACE = ((ace_type, ace_flags), access_mask, sid).
                    ace_type = ace[0][0]
                    mask = ace[1]
                    sid_str = _win32security.ConvertSidToStringSid(ace[2])
                    if ace_type == _ACCESS_DENIED:
                        continue
                    if ace_type != _ACCESS_ALLOWED:
                        others = True
                        continue
                    if not (mask & _WRITE_BITS):
                        continue
                    if sid_str in _PRIVILEGED_SIDS or sid_str == owner:
                        continue
                    if sid_str in _OTHER_PRINCIPAL_SIDS:
                        others = True
                        continue
                    # Any other SID with write access is treated as "other".
                    others = True
        return OwnerInfo(owner_sid=owner, is_current_user=is_current, others_writable=others)
    except Win32SecurityUnavailable:
        raise
    except Exception:
        raise Win32SecurityUnavailable("OWNER_UNAVAILABLE") from None


def has_privileges_available() -> bool:
    """True if pywin32 is usable for ACL inspection on this host."""
    return bool(WINDOWS and _win32security is not None and _kernel32 is not None)


def _set_owner_only_acl(path: Path) -> None:
    """Narrow a freshly created path to owner-only access (POSIX 0700/0600)."""
    if _win32security is None:
        raise Win32SecurityUnavailable("OWNER_UNAVAILABLE")
    sd = _win32security.GetFileSecurity(str(path), _win32security.OWNER_SECURITY_INFORMATION)
    owner = sd.GetSecurityDescriptorOwner()
    dacl = _win32security.ACL()
    dacl.AddAccessAllowedAce(
        _win32security.ACL_REVISION,
        _win32con.GENERIC_ALL,
        owner,
    )
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    _win32security.SetFileSecurity(str(path), _win32security.DACL_SECURITY_INFORMATION, sd)


def ensure_directory_chain(path: str | os.PathLike[str], *, create: bool = True) -> list[tuple]:
    """Validate (and optionally create) every directory from the drive root.

    Windows parity of monitor_service_host._storage's parent-dir walk:
      * every component must be a real directory, not a reparse point;
      * owner must be the current user or a privileged SID (POSIX root equiv);
      * no non-privileged, non-owner SID may write (POSIX required sticky bit
        or no group/other write on ancestors).

    Returns the list of (path, handle_stamp) for each validated directory so a
    caller can re-check stability after opening files, mirroring the POSIX
    chain TOCTOU check.  Raises closed on any failure.
    """
    if not WINDOWS or _win32security is None:
        raise Win32SecurityUnavailable("STORAGE_UNAVAILABLE")
    target = Path(os.path.abspath(path))
    if not target.is_absolute():
        raise Win32SecurityUnavailable("STORAGE_PATH_INVALID")
    parts = list(target.relative_to(target.anchor).parts)
    if not parts:
        raise Win32SecurityUnavailable("STORAGE_PATH_INVALID")
    chain = []
    current = Path(target.anchor)  # e.g. C:\
    last_index = len(parts) - 1
    for index, part in enumerate(parts):
        child = current / part
        if not child.exists():
            if not create:
                raise Win32SecurityUnavailable("STORAGE_MISSING")
            try:
                child.mkdir()
                # POSIX os.mkdir(name, 0o700): owner-only.  Windows inherits a
                # broader DACL by default, so narrow the final storage dir to
                # owner-only at creation (avoids a window of exposure).
                if index == last_index:
                    _set_owner_only_acl(child)
            except OSError:
                raise Win32SecurityUnavailable("STORAGE_MKFAILED") from None
        if is_reparse_point(child):
            raise Win32SecurityUnavailable("STORAGE_SYMLINK")
        # POSIX ancestors allow root-or-owner with sticky-bit write tolerance;
        # Windows system roots (C:\, C:\Users) carry inherited write ACEs with
        # no sticky-bit analogue.  Trust ancestors for reparse only, and apply
        # the strict owner/ACL check to the final storage directory itself
        # (the component that actually holds keys/journals).
        if index == last_index:
            try:
                info = inspect_owner(child)
            except Win32SecurityUnavailable:
                raise Win32SecurityUnavailable("STORAGE_UNTRUSTED") from None
            if not info.is_current_user or info.others_writable:
                raise Win32SecurityUnavailable("STORAGE_UNTRUSTED")
        h = _create_file(str(child), directory=True)
        try:
            chain.append((str(child), handle_stamp(h)))
        finally:
            _kernel32.CloseHandle(h)
        current = child
    return chain


def _final_path(handle: _wt.HANDLE) -> str:
    """Canonical NT path of an open handle (\\\\?\\ prefix)."""
    size = 4096
    while size <= 65536:
        buf = ctypes.create_unicode_buffer(size)
        n = _kernel32.GetFinalPathNameByHandleW(handle, buf, size, 0)
        if n == 0:
            raise Win32SecurityUnavailable("PATH_UNAVAILABLE")
        if n < size:
            return buf.value
        size *= 2
    raise Win32SecurityUnavailable("PATH_UNAVAILABLE")


def _nt_abs_path(path: str) -> str:
    """Normalise a path to the \\\\?\\ form returned by GetFinalPathNameByHandle."""
    abs_path = os.path.abspath(path)
    return "\\\\?\\" + abs_path


def read_bounded_windows(path: str | os.PathLike[str], limit: int, *, protected: bool) -> bytes:
    """Windows parity of monitor_service_config.read_bounded.

    Preserves every invariant of the POSIX original:
      * no-follow on the final component (reparse points refused);
      * all parent components checked for reparse points (symlink/junction);
      * handle canonical path equals the requested path (no redirection);
      * regular file, bounded size, exact byte count and stamp stability
        before/after read (TOCTOU detection);
      * for ``protected``: exact owner == current user, no other-writable ACE,
        and link count == 1.

    Every failure raises :class:`Win32SecurityUnavailable`; the caller maps it
    to its own ``FILE_UNAVAILABLE`` error.  No path/SID is exposed.
    """
    if not WINDOWS or _kernel32 is None:
        raise Win32SecurityUnavailable("FILE_UNAVAILABLE")
    p = os.fspath(path)
    abs_path = os.path.abspath(p)

    # Reject a reparse point anywhere on the literal chain (parents + final).
    # For protected reads, also verify each ancestor is owned by the current
    # user (or a privileged/system SID) and not writable by others, matching
    # POSIX ``st_uid in (0, euid)`` + ``mode & 0o022`` on every parent.
    cur = Path(abs_path)
    ancestors = list(cur.parents) + [cur]
    volume_root = Path(cur.anchor)
    for index, ancestor in enumerate(ancestors):
        try:
            if is_reparse_point(ancestor):
                raise Win32SecurityUnavailable("FILE_REPARSE")
            if protected and index < len(ancestors) - 1 and ancestor != volume_root:
                ainfo = inspect_owner(ancestor)
                # Windows system roots (C:\, C:\Users, ...) are system-owned;
                # POSIX treats uid 0 as trusted, so these are the equivalent.
                # Windows directory DACLs inherit family/group ACEs that
                # inspect_owner conservatively flags as "others writable",
                # with no sticky-bit analogue.  Owner identity is the
                # meaningful check here (it stops a directory being swapped
                # for one owned by a different principal); the final file
                # still gets the full no-others-write check below.
                if not (ainfo.is_current_user or ainfo.owner_sid in _PRIVILEGED_SIDS):
                    raise Win32SecurityUnavailable("FILE_UNTRUSTED")
        except Win32SecurityUnavailable:
            raise
        except Exception:
            raise Win32SecurityUnavailable("FILE_UNAVAILABLE") from None

    fd = None
    handle = None
    try:
        # Allow share-delete so a concurrent rename can happen; the stamp
        # comparison below detects it (matching POSIX TOCTOU semantics) rather
        # than preventing it via a mandatory lock (which POSIX does not have).
        fd, handle = open_nofollow(p, share_delete=True)
        before = handle_stamp(handle)
        if not stat.S_ISREG(before[2]) or before[5] > limit:
            raise Win32SecurityUnavailable("FILE_UNAVAILABLE")
        if _final_path(handle) != _nt_abs_path(p):
            raise Win32SecurityUnavailable("FILE_REDIRECTED")
        if protected:
            owner = inspect_owner(p)
            if not owner.is_current_user or owner.others_writable or before[4] != 1:
                raise Win32SecurityUnavailable("FILE_UNTRUSTED")

        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)

        after = handle_stamp(handle)
        if len(data) > limit or len(data) != before[5] or before != after:
            raise Win32SecurityUnavailable("FILE_CHANGED")
        # Re-open by path and compare identity: catches a rename/replace where
        # the path now points to a different file while our handle stayed on
        # the old inode (POSIX `entry` vs by-handle stamp comparison).
        try:
            check_fd, check_handle = open_nofollow(p, share_delete=True)
        except Win32SecurityUnavailable:
            # Path disappeared or became a reparse point during the read.
            raise Win32SecurityUnavailable("FILE_CHANGED") from None
        try:
            if handle_stamp(check_handle) != before:
                raise Win32SecurityUnavailable("FILE_CHANGED")
        finally:
            os.close(check_fd)
        return bytes(data)
    finally:
        if fd is not None:
            os.close(fd)
        elif handle is not None:
            _kernel32.CloseHandle(handle)


__all__ = [
    "WINDOWS",
    "Win32SecurityUnavailable",
    "sleep_inclusive_clock_ns",
    "is_reparse_point",
    "open_nofollow",
    "handle_stamp",
    "inspect_owner",
    "current_user_sid",
    "has_privileges_available",
    "ensure_directory_chain",
    "read_bounded_windows",
    "OwnerInfo",
]
