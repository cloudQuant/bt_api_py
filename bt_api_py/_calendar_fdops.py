"""fd-level file primitives for the trading-calendar retention workflow.

On POSIX every helper is a thin pass-through to the exact original ``os.*``
call, so the security-critical code path keeps its ``dir_fd`` /
``O_NOFOLLOW`` semantics unchanged.

On Windows there is no ``dir_fd`` and no ``O_NOFOLLOW`` / ``O_DIRECTORY``.
The helpers below open the *absolute* path with ``CreateFileW`` in
reparse-open mode and re-establish, on every open, the invariants the POSIX
calls provide:

* the opened object is the file the path names, not a symlink / junction
  (reparse attributes are refused on the path and on the open handle);
* ``GetFinalPathNameByHandleW`` canonical path equals the requested absolute
  path, so no reparse component in the chain can redirect the open;
* the by-handle identity stamp taken at open equals a fresh re-open by path
  (POSIX ``dir_fd`` is inode-bound; this re-open comparison is the Windows
  TOCTOU detection for a path swapped between check and use);
* owner-only DACL (the 0700/0600 equivalent) on every freshly created entry.

``_WinStat`` encodes the POSIX permission bits (0700 directories, 0600 files)
so the unchanged ``st_mode & 0o077`` / ``S_IMODE == 0o700`` / ``st_uid ==
geteuid()`` checks keep their meaning.

Every failure raises a category-only exception (no path, SID or account value
is ever included).  There is deliberately no fallback to a weaker check.
"""

from __future__ import annotations

import os
import stat as _stat

WINDOWS = os.name == "nt"

# On POSIX these are the real flags.  On Windows the originals do not exist;
# synthetic non-zero values let callers keep writing ``flags | O_DIRECTORY``
# and let open_at tell a directory open from a file open.
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0o400000)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0o0200000)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0o4000)

_OWNER_UID = 1
_OTHER_UID = 2
# 1601-01-01 -> 1970-01-01 = 134774 days, in nanoseconds.
_FILETIME_TO_UNIX_NS = 11644473600000000000


class FdOpsError(Exception):
    """A platform-neutral, category-only fd-operation failure."""

    def __init__(self, code: str = "INVALID_STORAGE") -> None:
        super().__init__(code)
        self.code = code


class FdRef:
    """A CRT fd plus, on Windows, its native handle and identity stamp."""

    __slots__ = ("fd", "handle", "stamp", "path", "is_dir")

    def __init__(self, fd, handle=None, stamp=None, path=None, is_dir=False) -> None:
        self.fd = fd
        self.handle = handle
        self.stamp = stamp
        self.path = path
        self.is_dir = is_dir

    def __index__(self) -> int:
        return self.fd


class _WinStat:
    """Minimal ``os.stat_result`` shape used by the unchanged calendar checks."""

    __slots__ = ("st_mode", "st_uid", "st_gid", "st_ino", "st_dev", "st_nlink",
                 "st_size", "st_mtime", "st_ctime", "st_atime",
                 "st_mtime_ns", "st_ctime_ns", "st_atime_ns", "st_file_attributes")

    def __init__(self, stamp, path) -> None:
        owner = _owner(path)
        base_mode = stamp[2]
        is_dir = _stat.S_ISDIR(base_mode)
        # POSIX mode the original code expects: 0700 for directories, 0600 for
        # files.  Any other-writable ACE adds all group/other write bits so the
        # unchanged ``st_mode & 0o077`` and ``S_IMODE(...) == 0o700`` checks
        # still reject it; a non-owner gets a different uid.
        perm = 0o700 if is_dir else 0o600
        if not owner.is_current_user:
            perm |= 0o007 if is_dir else 0o006
        if owner.others_writable:
            perm |= 0o077
        self.st_mode = base_mode | perm
        self.st_uid = _OWNER_UID if owner.is_current_user else _OTHER_UID
        self.st_gid = self.st_uid
        self.st_ino = stamp[1]
        self.st_dev = stamp[0]
        self.st_nlink = stamp[4]
        self.st_size = stamp[5]
        # FILETIME counts 100ns ticks since 1601-01-01; POSIX stat times are
        # nanoseconds since 1970-01-01.  Subtract the 134774-day epoch offset
        # so fstat/fdops stamps are directly comparable with os.stat results
        # (and render sane mtimes) instead of being ~369 years in the future.
        self.st_mtime_ns = stamp[6] * 100 - _FILETIME_TO_UNIX_NS
        self.st_ctime_ns = stamp[7] * 100 - _FILETIME_TO_UNIX_NS
        self.st_atime_ns = self.st_mtime_ns
        self.st_mtime = self.st_mtime_ns / 1_000_000_000
        self.st_ctime = self.st_ctime_ns / 1_000_000_000
        self.st_atime = self.st_mtime
        self.st_file_attributes = 0


# ---------------------------------------------------------------------------
# Windows native bindings
# ---------------------------------------------------------------------------

_k32 = None
_w32 = None
_msvcrt = None
_WIN_OK = False

if WINDOWS:  # pragma: no cover - platform gated
    import ctypes
    import ctypes.wintypes as _wt

    from bt_api_py import _win32_security as _w32

    _k32 = _w32._kernel32
    import msvcrt as _msvcrt

    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # READ | WRITE | DELETE
    _OPEN_EXISTING = 3
    _CREATE_NEW = 1
    _OPEN_ALWAYS = 4
    _INVALID_HANDLE = _w32._INVALID_HANDLE_VALUE
    _ERR_NOT_FOUND = (2, 3)
    _ERR_EXISTS = (80, 183)
    _ERR_BUSY = (32, 33)
    _REPARSE_ATTR = 0x400
    _DIR_ATTR = 0x10
    _WIN_OK = bool(_k32 is not None and _msvcrt is not None)

    if _k32 is not None:
        _k32.CreateHardLinkW.restype = _wt.BOOL
        _k32.CreateHardLinkW.argtypes = [_wt.LPCWSTR, _wt.LPCWSTR, _wt.LPVOID]
        _k32.CreateDirectoryW.restype = _wt.BOOL
        _k32.CreateDirectoryW.argtypes = [_wt.LPCWSTR, _wt.LPVOID]


def _create_directory(path: str) -> None:
    import ctypes

    ctypes.set_last_error(0)
    if not _k32.CreateDirectoryW(path, None):
        err = ctypes.get_last_error()
        if err in _ERR_EXISTS:
            raise FileExistsError()
        raise FdOpsError()


def _owner(path: str):
    try:
        return _w32.inspect_owner(path)
    except _w32.Win32SecurityUnavailable as exc:
        raise FdOpsError() from exc


def _set_owner_only(path: str) -> None:
    from pathlib import Path

    try:
        _w32._set_owner_only_acl(Path(path))
    except _w32.Win32SecurityUnavailable as exc:
        raise FdOpsError() from exc


def _paths_equal(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def _check_ancestors(path: str) -> None:
    """Refuse a reparse point in any parent component of ``path``."""
    from pathlib import Path

    for ancestor in Path(path).parents:
        try:
            if _w32.is_reparse_point(ancestor):
                raise FdOpsError()
        except _w32.Win32SecurityUnavailable as exc:
            raise FdOpsError() from exc


def _open_handle(path: str, *, write: bool, create_new: bool = False,
                 create: bool = False, directory: bool = False,
                 check_type: bool = True):
    """Return ``(native_handle, stamp, absolute_path)`` with nofollow enforced."""
    if not _WIN_OK:
        raise FdOpsError()
    p = os.path.abspath(os.fspath(path))
    if not create_new and os.path.lexists(p):
        try:
            if _w32.is_reparse_point(p):
                raise FdOpsError()
        except _w32.Win32SecurityUnavailable as exc:
            raise FdOpsError() from exc
    flags = (_FILE_FLAG_OPEN_REPARSE_POINT
             | (_FILE_FLAG_BACKUP_SEMANTICS if (directory or not check_type) else 0))
    access = _GENERIC_READ | (_GENERIC_WRITE if write else 0)
    disposition = _CREATE_NEW if create_new else (_OPEN_ALWAYS if create else _OPEN_EXISTING)
    import ctypes

    ctypes.set_last_error(0)
    handle = _k32.CreateFileW(p, access, _FILE_SHARE_ALL, None, disposition, flags, None)
    if not handle or handle == _INVALID_HANDLE:
        err = ctypes.get_last_error()
        if err in _ERR_NOT_FOUND:
            raise FileNotFoundError()
        if err in _ERR_EXISTS:
            raise FileExistsError()
        if err in _ERR_BUSY:
            raise FdOpsError("ACCOUNT_BUSY")
        raise FdOpsError()
    try:
        info = _w32._ByHandleFileInformation()
        if not _k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise FdOpsError()
        if info.dwFileAttributes & _REPARSE_ATTR:
            raise FdOpsError()
        is_dir = bool(info.dwFileAttributes & _DIR_ATTR)
        if check_type and is_dir != directory:
            raise FdOpsError()
        try:
            final = _w32._final_path(handle)
        except _w32.Win32SecurityUnavailable as exc:
            raise FdOpsError() from exc
        if not _paths_equal(final, _w32._nt_abs_path(p)):
            raise FdOpsError()
        stamp = _w32.handle_stamp(handle)
        return handle, stamp, p
    except BaseException:
        _k32.CloseHandle(handle)
        raise


def _reopen_stable(path: str, stamp: tuple, *, directory: bool = False,
                   check_type: bool = True) -> None:
    """Re-open by path and require the same by-handle identity (TOCTOU)."""
    handle, current, _ = _open_handle(path, write=False, directory=directory,
                                      check_type=check_type)
    try:
        if current != stamp:
            raise FdOpsError()
    finally:
        _k32.CloseHandle(handle)


def _verify_root_stable(root_ref, root_path: str) -> None:
    if not _WIN_OK:
        raise FdOpsError()
    p = os.path.abspath(os.fspath(root_path))
    handle, stamp, _ = _open_handle(p, write=False, directory=True)
    try:
        if stamp != _w32.handle_stamp(root_ref.handle):
            raise FdOpsError()
    finally:
        _k32.CloseHandle(handle)


def _ref_path(root_ref) -> str:
    if not isinstance(root_ref, FdRef):
        raise FdOpsError()
    return root_ref.path


def _child_path(root_ref, name) -> str:
    root_path = _ref_path(root_ref)
    p = os.path.abspath(os.path.join(root_path, os.fspath(name)))
    if os.path.dirname(p) != root_path:
        raise FdOpsError()
    return p


# ---------------------------------------------------------------------------
# Drop-in ``os``-style wrappers (keep the original call shape, including dir_fd)
# ---------------------------------------------------------------------------

def open(name, flags, mode=0o777, *, dir_fd=None):
    """``os.open`` with dir_fd; Windows routes to open_at / open_path."""
    if not WINDOWS:
        return os.open(name, flags, mode, dir_fd=dir_fd)
    if dir_fd is None:
        return open_path(name, flags, mode)
    return open_at(dir_fd, name, flags, mode)


def stat(name, *, dir_fd=None, follow_symlinks=True):
    """``os.stat`` with dir_fd/follow_symlinks; Windows refuses reparse points."""
    if not WINDOWS:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
    if dir_fd is None:
        return lstat_path(name)
    return lstat_at(dir_fd, name)


def mkdir(name, mode=0o777, *, dir_fd=None):
    """``os.mkdir`` with dir_fd; Windows narrows the DACL to owner-only."""
    if not WINDOWS:
        return os.mkdir(name, mode, dir_fd=dir_fd)
    if dir_fd is None:
        p = os.path.abspath(os.fspath(name))
        _check_ancestors(p)
        _create_directory(p)
        handle, stamp, canonical = _open_handle(p, write=False, directory=True)
        try:
            _set_owner_only(p)
            _reopen_stable(p, stamp, directory=True)
            fd = _msvcrt.open_osfhandle(handle, os.O_RDONLY)
            if fd < 0:
                raise FdOpsError()
            return FdRef(fd, handle, stamp, canonical, is_dir=True)
        except BaseException:
            _k32.CloseHandle(handle)
            raise
    return mkdir_at(dir_fd, name, mode)


def unlink(name, *, dir_fd=None):
    """``os.unlink`` with dir_fd."""
    if not WINDOWS:
        return os.unlink(name, dir_fd=dir_fd)
    if dir_fd is None:
        p = os.path.abspath(os.fspath(name))
        _check_ancestors(p)
        os.unlink(p)
        return None
    return unlink_at(dir_fd, name)


def link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
    """``os.link`` with src_dir_fd/dst_dir_fd."""
    if not WINDOWS:
        return os.link(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd,
                       follow_symlinks=follow_symlinks)
    root = dst_dir_fd if dst_dir_fd is not None else src_dir_fd
    if root is None:
        raise FdOpsError()
    return link_at(root, src, dst, follow_symlinks=follow_symlinks)


# ---------------------------------------------------------------------------
# Platform-neutral public helpers
# ---------------------------------------------------------------------------

def open_root(path: str, *, write: bool = False, create: bool = False,
              check_owner: bool = True):
    """Open a directory as a stable root reference (POSIX dir_fd equivalent)."""
    if not WINDOWS:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        return os.open(path, flags)
    p = os.path.abspath(os.fspath(path))
    try:
        if _w32.is_reparse_point(p):
            raise FdOpsError()
    except _w32.Win32SecurityUnavailable as exc:
        raise FdOpsError() from exc
    if create:
        _create_directory(p)
    handle, stamp, canonical = _open_handle(p, write=write, directory=True)
    try:
        if create:
            _set_owner_only(p)
        if check_owner:
            info = _owner(p)
            if not info.is_current_user or info.others_writable:
                raise FdOpsError()
        fd = _msvcrt.open_osfhandle(handle, os.O_RDWR if write else os.O_RDONLY)
        if fd < 0:
            raise FdOpsError()
        return FdRef(fd, handle, stamp, canonical, is_dir=True)
    except BaseException:
        _k32.CloseHandle(handle)
        raise


def open_at(root_ref, name, flags, mode=None):
    """POSIX ``os.open(name, flags, mode, dir_fd=root_fd)`` equivalent."""
    if not WINDOWS:
        if mode is None:
            return os.open(name, flags, dir_fd=root_ref)
        return os.open(name, flags, mode, dir_fd=root_ref)
    p = _child_path(root_ref, name)
    _check_ancestors(p)
    directory = bool(flags & O_DIRECTORY)
    write = bool(flags & 3)  # O_RDONLY=0, O_WRONLY=1, O_RDWR=2
    create_new = bool((flags & os.O_CREAT) and (flags & os.O_EXCL))
    create = bool(flags & os.O_CREAT)
    existed = os.path.lexists(p)
    handle, stamp, canonical = _open_handle(
        p, write=write, create_new=create_new, create=create, directory=directory)
    try:
        _reopen_stable(p, stamp, directory=directory)
        if create and not existed:
            _set_owner_only(p)
        fd = _msvcrt.open_osfhandle(handle, os.O_RDWR if write else os.O_RDONLY)
        if fd < 0:
            raise FdOpsError()
        return FdRef(fd, handle, stamp, canonical, is_dir=directory)
    except BaseException:
        _k32.CloseHandle(handle)
        raise


def open_path(path, flags, mode=None):
    """POSIX ``os.open(path, flags)`` with nofollow enforced on Windows."""
    if not WINDOWS:
        if mode is None:
            return os.open(path, flags)
        return os.open(path, flags, mode)
    p = os.path.abspath(os.fspath(path))
    _check_ancestors(p)
    directory = bool(flags & O_DIRECTORY)
    write = bool(flags & 3)
    create_new = bool((flags & os.O_CREAT) and (flags & os.O_EXCL))
    create = bool(flags & os.O_CREAT)
    existed = os.path.lexists(p)
    handle, stamp, canonical = _open_handle(
        p, write=write, create_new=create_new, create=create, directory=directory)
    try:
        _reopen_stable(p, stamp, directory=directory)
        if create and not existed:
            _set_owner_only(p)
        fd = _msvcrt.open_osfhandle(handle, os.O_RDWR if write else os.O_RDONLY)
        if fd < 0:
            raise FdOpsError()
        return FdRef(fd, handle, stamp, canonical, is_dir=directory)
    except BaseException:
        _k32.CloseHandle(handle)
        raise


def mkdir_at(root_ref, name, mode=0o700):
    """POSIX ``os.mkdir(name, mode, dir_fd=root_fd)`` equivalent."""
    if not WINDOWS:
        return os.mkdir(name, mode, dir_fd=root_ref)
    p = _child_path(root_ref, name)
    _check_ancestors(p)
    _create_directory(p)
    handle, stamp, canonical = _open_handle(p, write=False, directory=True)
    try:
        _set_owner_only(p)
        _reopen_stable(p, stamp, directory=True)
        fd = _msvcrt.open_osfhandle(handle, os.O_RDONLY)
        if fd < 0:
            raise FdOpsError()
        return FdRef(fd, handle, stamp, canonical, is_dir=True)
    except BaseException:
        _k32.CloseHandle(handle)
        raise


def unlink_at(root_ref, name) -> None:
    """POSIX ``os.unlink(name, dir_fd=root_fd)`` equivalent."""
    if not WINDOWS:
        return os.unlink(name, dir_fd=root_ref)
    p = _child_path(root_ref, name)
    _verify_root_stable(root_ref, root_ref.path)
    _check_ancestors(p)
    os.unlink(p)


def link_at(root_ref, src, name, *, follow_symlinks=False) -> None:
    """POSIX ``os.link(src, name, src_dir_fd=.., dst_dir_fd=..)`` equivalent."""
    if not WINDOWS:
        return os.link(src, name, src_dir_fd=root_ref, dst_dir_fd=root_ref,
                       follow_symlinks=follow_symlinks)
    p_src = _child_path(root_ref, src)
    p_dst = _child_path(root_ref, name)
    _verify_root_stable(root_ref, root_ref.path)
    _check_ancestors(p_src)
    _check_ancestors(p_dst)
    source = open_at(root_ref, src, os.O_RDONLY | O_NOFOLLOW)
    try:
        info = fstat(source)
        if (not _stat.S_ISREG(info.st_mode) or info.st_uid != geteuid()
                or info.st_mode & 0o077):
            raise FdOpsError()
        if os.path.lexists(p_dst):
            try:
                if _w32.is_reparse_point(p_dst):
                    raise FdOpsError()
            except _w32.Win32SecurityUnavailable as exc:
                raise FdOpsError() from exc
            raise FileExistsError()
        import ctypes

        ctypes.set_last_error(0)
        if not _k32.CreateHardLinkW(p_dst, p_src, None):
            err = ctypes.get_last_error()
            if err in _ERR_EXISTS:
                raise FileExistsError()
            raise FdOpsError()
        linked = lstat_path(p_dst)
        if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
            raise FdOpsError()
    except _w32.Win32SecurityUnavailable as exc:
        raise FdOpsError() from exc
    finally:
        close(source)


def lstat_at(root_ref, name):
    """POSIX ``os.stat(name, dir_fd=root_fd, follow_symlinks=False)`` equivalent."""
    if not WINDOWS:
        return os.stat(name, dir_fd=root_ref, follow_symlinks=False)
    p = _child_path(root_ref, name)
    _check_ancestors(p)
    handle, stamp, _ = _open_handle(p, write=False, check_type=False)
    try:
        return _WinStat(stamp, p)
    finally:
        _k32.CloseHandle(handle)


def lstat_path(path):
    """POSIX ``os.stat(path, follow_symlinks=False)`` equivalent."""
    if not WINDOWS:
        return os.stat(path, follow_symlinks=False)
    p = os.path.abspath(os.fspath(path))
    _check_ancestors(p)
    handle, stamp, _ = _open_handle(p, write=False, check_type=False)
    try:
        return _WinStat(stamp, p)
    finally:
        _k32.CloseHandle(handle)


def lstat_root(root_ref, root_path: str):
    """POSIX ``os.stat(root, follow_symlinks=False)`` equivalent."""
    if not WINDOWS:
        return os.stat(root_path, follow_symlinks=False)
    _verify_root_stable(root_ref, root_path)
    return _WinStat(_w32.handle_stamp(root_ref.handle), root_ref.path)


def listdir(ref, root_path=None):
    if not WINDOWS:
        return os.listdir(ref)
    if not isinstance(ref, FdRef):
        return os.listdir(ref)
    path = root_path if root_path is not None else _ref_path(ref)
    _verify_root_stable(ref, path)
    return os.listdir(path)


def scandir(ref, root_path=None):
    if not WINDOWS:
        return os.scandir(ref)
    if not isinstance(ref, FdRef):
        return os.scandir(ref)
    path = root_path if root_path is not None else _ref_path(ref)
    _verify_root_stable(ref, path)
    return os.scandir(path)


def fstat(ref):
    if not WINDOWS:
        return os.fstat(ref)
    if not isinstance(ref, FdRef):
        raise FdOpsError()
    return _WinStat(_w32.handle_stamp(ref.handle), ref.path)


def fchmod(ref, mode: int) -> None:
    """POSIX ``os.fchmod`` equivalent; Windows enforces owner-only ACL."""
    if not WINDOWS:
        return os.fchmod(ref, mode)
    if not isinstance(ref, FdRef):
        raise FdOpsError()
    _set_owner_only(ref.path)


def pread(ref, size: int, offset: int) -> bytes:
    if not WINDOWS:
        return os.pread(ref, size, offset)
    fd = raw(ref)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, size)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def read(ref, size: int) -> bytes:
    return os.read(raw(ref), size)


def write(ref, data: bytes) -> int:
    return os.write(raw(ref), data)


def fsync(ref) -> None:
    if not WINDOWS:
        os.fsync(ref)
        return
    if isinstance(ref, FdRef) and ref.is_dir:
        # Windows has no directory fsync (FlushFileBuffers fails on a directory
        # handle).  NTFS journals directory metadata on handle close.
        return
    os.fsync(raw(ref))


def close(ref) -> None:
    os.close(raw(ref))


def raw(ref):
    return ref.fd if isinstance(ref, FdRef) else ref


def getuid() -> int:
    return os.getuid() if not WINDOWS else _OWNER_UID


def geteuid() -> int:
    return os.geteuid() if not WINDOWS else _OWNER_UID


class FcntlShim:
    """``fcntl.flock`` subset backed by ``msvcrt`` byte-range locking."""

    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def flock(self, fd, operation) -> None:
        if not WINDOWS:  # pragma: no cover - shim is Windows-only
            import fcntl as _real
            return _real.flock(fd, operation)
        raw_fd = getattr(fd, "fd", fd)
        if operation & self.LOCK_UN:
            mode = _msvcrt.LK_UNLCK
        elif operation & self.LOCK_EX:
            mode = _msvcrt.LK_NBLCK if operation & self.LOCK_NB else _msvcrt.LK_LOCK
        else:
            mode = _msvcrt.LK_NBRLCK if operation & self.LOCK_NB else _msvcrt.LK_RLCK
        os.lseek(raw_fd, 0, os.SEEK_SET)
        try:
            _msvcrt.locking(raw_fd, mode, 1)
        except OSError as exc:
            # Normalize a contended non-blocking lock to the POSIX exception
            # the unchanged callers already catch.
            if operation & self.LOCK_NB:
                raise BlockingIOError() from exc
            raise


fcntl = FcntlShim()


__all__ = [
    "WINDOWS", "O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK",
    "FdOpsError", "FdRef", "open", "stat", "mkdir", "unlink", "link",
    "open_root", "open_at", "open_path", "mkdir_at",
    "unlink_at", "link_at", "lstat_at", "lstat_path", "lstat_root", "listdir",
    "scandir", "fstat", "fchmod", "pread", "read", "write", "fsync", "close",
    "raw", "getuid", "geteuid", "FcntlShim", "fcntl",
]
