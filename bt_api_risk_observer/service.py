"""Standalone, observe-only checks for an existing Risk SQLite store.

This module deliberately has no imports from ``bt_api_py`` or ``bt_api_risk``.
It only resolves the public ``storage.directory`` setting and reads bounded
SQLite schema metadata. It does not validate Risk schema or readiness.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sqlite3
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO
from uuid import uuid4

_CONFIG_LIMIT = 1_048_576
_STOP_MARKER_LIMIT = 64
_STOP_MARKER_INITIAL = b"RISK OBSERVER WAITING\n"
_DATABASE_LIMIT = 2 * 1024 * 1024 * 1024
_SCHEMA_OBJECT_LIMIT = 64
_SCHEMA_TYPE_CHAR_LIMIT = 16
_SCHEMA_TYPE_BYTE_LIMIT = 48
_SCHEMA_NAME_CHAR_LIMIT = 200
_SCHEMA_NAME_BYTE_LIMIT = 512
_SCHEMA_METADATA_BYTE_LIMIT = 32_768
_SQLITE_VALUE_LIMIT = 4_096
_SQLITE_BUSY_TIMEOUT_SECONDS = 0.05
_SQLITE_PROGRESS_OPCODES = 1_000
_SQLITE_MAX_PROGRESS_CALLBACKS = 100
_POLL_INTERVAL_SECONDS = 0.5
_SERVICE_SCHEMA = "bt_api_risk.service.v1"
_YAML_NODE_LIMIT = 100_000
_YAML_DEPTH_LIMIT = 64

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WINDOWS_WRITE_MASK = (
    0x40000000  # GENERIC_WRITE
    | 0x0002  # FILE_WRITE_DATA
    | 0x0004  # FILE_APPEND_DATA
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
    | 0x00010000  # DELETE
    | 0x0040  # DELETE_CHILD
)
_WINDOWS_PRIVATE_ACCESS_MASK = _WINDOWS_WRITE_MASK | 0x80000000 | 0x10000000 | 0x0001 | 0x0008
_PRIVILEGED_SIDS = {"S-1-5-18", "S-1-5-32-544"}  # SYSTEM, Administrators


class RiskObservationError(RuntimeError):
    """Finite, path-free observer failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RiskObservationError(code)


@dataclass(frozen=True)
class RiskObservation:
    state: str
    mode: str
    storage_health: str
    risk_readiness: str
    account_readiness: str
    market_readiness: str
    sqlite_schema_version: int
    sqlite_user_version: int
    schema_object_count: int
    risk_schema_validation: str


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _windows_kernel():
    if os.name != "nt":
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetFileAttributesW.restype = wintypes.DWORD
        kernel.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        return kernel
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_attributes(path: Path) -> int:
    try:
        value = _windows_kernel().GetFileAttributesW(os.fspath(path))
        if value == 0xFFFFFFFF or value & _FILE_ATTRIBUTE_REPARSE_POINT:
            _fail("RISK_OBSERVER_REPARSE_REFUSED")
        return int(value)
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_path_chain(path: Path, *, final_directory: bool) -> None:
    """Reject missing, redirected, or non-directory components on Windows."""
    absolute = Path(os.path.abspath(path))
    components = list(reversed(absolute.parents)) + [absolute]
    try:
        for index, component in enumerate(components):
            attributes = _windows_attributes(component)
            is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
            if index < len(components) - 1 or final_directory:
                if not is_directory:
                    _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")
            elif is_directory:
                _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_open(path: Path, *, directory: bool = False) -> tuple[int, object]:
    """Open a Win32 identity handle without following the final reparse point."""
    try:
        import msvcrt

        kernel = _windows_kernel()
        attributes = _windows_attributes(path)
        if bool(attributes & _FILE_ATTRIBUTE_DIRECTORY) != directory:
            _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
        flags = _FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            flags |= _FILE_FLAG_BACKUP_SEMANTICS
        # Do not share delete/rename while SQLite uses this identity-pinned file.
        handle = kernel.CreateFileW(
            os.fspath(path),
            _GENERIC_READ,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            flags,
            None,
        )
        if not handle or handle == _INVALID_HANDLE_VALUE:
            _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        if fd < 0:
            kernel.CloseHandle(handle)
            _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
        return fd, kernel
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_handle_stamp(fd: int) -> tuple[int, int, int, int, int, int]:
    try:
        import msvcrt

        kernel = _windows_kernel()
        info = _ByHandleFileInformation()
        if not kernel.GetFileInformationByHandle(msvcrt.get_osfhandle(fd), ctypes.byref(info)):
            _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")
        index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
        size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
        return (
            int(info.dwVolumeSerialNumber),
            index,
            int(info.dwFileAttributes),
            int(info.nNumberOfLinks),
            size,
            int(info.ftLastWriteTime.dwLowDateTime)
            | (int(info.ftLastWriteTime.dwHighDateTime) << 32),
        )
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_acl_state(path: Path) -> tuple[str, str, bool]:
    """Return owner SID, process SID, and whether non-owner writes are granted."""
    try:
        import win32api
        import win32con
        import win32security

        flags = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
        descriptor = win32security.GetFileSecurity(os.fspath(path), flags)
        owner = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            current_sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            )
        finally:
            win32api.CloseHandle(token)
        dacl = descriptor.GetSecurityDescriptorDacl()
        if dacl is None:
            _fail("RISK_OBSERVER_FILE_UNPROTECTED")
        others_writable = False
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            ace_type, mask, sid = ace[0][0], int(ace[1]), ace[2]
            if ace_type != 0:  # unknown/callback ACE types fail closed below
                if ace_type not in (1,):  # ACCESS_DENIED_ACE_TYPE grants nothing
                    others_writable = True
                continue
            if mask & _WINDOWS_PRIVATE_ACCESS_MASK:
                sid_text = win32security.ConvertSidToStringSid(sid)
                if sid_text != owner and sid_text not in _PRIVILEGED_SIDS:
                    others_writable = True
        return owner, current_sid, others_writable
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_set_owner_only_acl(path: Path) -> None:
    """Replace a new marker's DACL with an owner-only ACL, failing closed."""
    try:
        import win32security

        descriptor = win32security.GetFileSecurity(
            os.fspath(path), win32security.OWNER_SECURITY_INFORMATION
        )
        owner = descriptor.GetSecurityDescriptorOwner()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, 0x10000000, owner)
        descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            os.fspath(path), win32security.DACL_SECURITY_INFORMATION, descriptor
        )
    except Exception:
        _fail("RISK_OBSERVER_WINDOWS_SECURITY_UNAVAILABLE")


def _windows_private_file(path: Path) -> None:
    """Testable fail-closed ACL gate shared by the database and stop marker."""
    owner, current, others_writable = _windows_acl_state(path)
    if owner != current or others_writable:
        _fail("RISK_OBSERVER_FILE_UNPROTECTED")


def _posix_stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_uid),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _validate_file_info(info: os.stat_result, *, private: bool, size_limit: int) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > size_limit:
        _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
    if private and (
        not hasattr(os, "geteuid") or info.st_uid != os.geteuid() or info.st_mode & 0o077
    ):
        _fail("RISK_OBSERVER_FILE_UNPROTECTED")


@contextmanager
def _storage_directory(path: Path) -> Iterator[tuple[int, tuple[int, int]]]:
    """Pin the configured storage directory and require owner-private access."""
    absolute = Path(os.path.abspath(path))
    if os.name == "nt":
        fd = None
        try:
            _windows_path_chain(absolute, final_directory=True)
            fd, _kernel = _windows_open(absolute, directory=True)
            identity = _validate_windows_stamp(
                _windows_handle_stamp(fd), directory=True, size_limit=0
            )
            _windows_private_file(absolute)
            yield fd, identity
            after = _validate_windows_stamp(_windows_handle_stamp(fd), directory=True, size_limit=0)
            if after != identity:
                _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
            _windows_path_chain(absolute, final_directory=True)
            check_fd, _kernel = _windows_open(absolute, directory=True)
            try:
                current = _validate_windows_stamp(
                    _windows_handle_stamp(check_fd), directory=True, size_limit=0
                )
                if current != identity:
                    _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
            finally:
                os.close(check_fd)
            _windows_private_file(absolute)
        except RiskObservationError:
            raise
        except Exception:
            _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")
        finally:
            if fd is not None:
                os.close(fd)
        return

    if os.name != "posix" or not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY")):
        _fail("RISK_OBSERVER_NOFOLLOW_UNAVAILABLE")
    descriptors: list[int] = []
    chain: list[tuple[int, str, int, tuple[int, int]]] = []
    try:
        root_fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root_fd)
        parent_fd = root_fd
        for name in absolute.parts[1:]:
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            descriptors.append(child_fd)
            child = os.fstat(child_fd)
            if not stat.S_ISDIR(child.st_mode):
                _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")
            chain.append((parent_fd, name, child_fd, (int(child.st_dev), int(child.st_ino))))
            parent_fd = child_fd
        info = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or not hasattr(os, "geteuid")
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            _fail("RISK_OBSERVER_STORAGE_UNPROTECTED")
        identity = (int(info.st_dev), int(info.st_ino))
        yield parent_fd, identity
        after = os.fstat(parent_fd)
        if (
            (int(after.st_dev), int(after.st_ino)) != identity
            or not stat.S_ISDIR(after.st_mode)
            or after.st_uid != os.geteuid()
            or after.st_mode & 0o077
        ):
            _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
        for parent, name, child_fd, child_identity in chain:
            child = os.fstat(child_fd)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (int(child.st_dev), int(child.st_ino)) != child_identity
                or (int(entry.st_dev), int(entry.st_ino)) != child_identity
                or not stat.S_ISDIR(child.st_mode)
            ):
                _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _validate_windows_stamp(
    stamp: tuple[int, int, int, int, int, int], *, directory: bool, size_limit: int
) -> tuple[int, int]:
    volume, index, attributes, links, size, _modified = stamp
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        _fail("RISK_OBSERVER_REPARSE_REFUSED")
    is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    if is_directory != directory or (not directory and (links != 1 or size > size_limit)):
        _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
    return volume, index


@contextmanager
def _regular_file(
    path: Path,
    *,
    size_limit: int,
    private: bool,
    parent_fd: int | None = None,
    parent_identity: tuple[int, int] | None = None,
) -> Iterator[tuple[int, tuple[int, int]]]:
    """Hold one no-follow file handle and verify identity/permissions afterward."""
    absolute = Path(os.path.abspath(path))
    if os.name == "nt":
        fd = None
        try:
            _windows_path_chain(absolute.parent, final_directory=True)
            fd, _kernel = _windows_open(absolute)
            stamp = _windows_handle_stamp(fd)
            identity = _validate_windows_stamp(stamp, directory=False, size_limit=size_limit)
            if private:
                _windows_private_file(absolute)
            yield fd, identity
            after = _windows_handle_stamp(fd)
            if _validate_windows_stamp(after, directory=False, size_limit=size_limit) != identity:
                _fail("RISK_OBSERVER_FILE_IDENTITY_CHANGED")
            if private:
                _windows_private_file(absolute)
            _windows_path_chain(absolute.parent, final_directory=True)
            path_fd, _kernel = _windows_open(absolute)
            try:
                current = _validate_windows_stamp(
                    _windows_handle_stamp(path_fd), directory=False, size_limit=size_limit
                )
                if current != identity:
                    _fail("RISK_OBSERVER_FILE_IDENTITY_CHANGED")
            finally:
                os.close(path_fd)
        except RiskObservationError:
            raise
        except Exception:
            _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
        finally:
            if fd is not None:
                os.close(fd)
        return

    if os.name != "posix" or not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY")):
        _fail("RISK_OBSERVER_NOFOLLOW_UNAVAILABLE")

    descriptors: list[int] = []
    file_fd: int | None = None
    chain: list[tuple[int, str, int, tuple[int, int]]] = []
    try:
        root_fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root_fd)
        parent_fd = root_fd
        if parent_fd is None:
            selected_parent_fd = root_fd
            for name in absolute.parts[1:-1]:
                child_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=selected_parent_fd,
                )
                descriptors.append(child_fd)
                child = os.fstat(child_fd)
                if not stat.S_ISDIR(child.st_mode):
                    _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
                chain.append(
                    (
                        selected_parent_fd,
                        name,
                        child_fd,
                        (int(child.st_dev), int(child.st_ino)),
                    )
                )
                selected_parent_fd = child_fd
        else:
            selected_parent_fd = parent_fd
            parent_info = os.fstat(parent_fd)
            selected_identity = (int(parent_info.st_dev), int(parent_info.st_ino))
            if (
                not stat.S_ISDIR(parent_info.st_mode)
                or parent_identity is None
                or selected_identity != parent_identity
            ):
                _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
        file_fd = os.open(
            absolute.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=selected_parent_fd,
        )
        before = os.fstat(file_fd)
        _validate_file_info(before, private=private, size_limit=size_limit)
        identity = (int(before.st_dev), int(before.st_ino))
        entry = os.stat(absolute.name, dir_fd=selected_parent_fd, follow_symlinks=False)
        if (int(entry.st_dev), int(entry.st_ino)) != identity:
            _fail("RISK_OBSERVER_FILE_IDENTITY_CHANGED")
        yield file_fd, identity
        after = os.fstat(file_fd)
        entry = os.stat(absolute.name, dir_fd=selected_parent_fd, follow_symlinks=False)
        _validate_file_info(after, private=private, size_limit=size_limit)
        if (int(after.st_dev), int(after.st_ino)) != identity or (
            int(entry.st_dev),
            int(entry.st_ino),
        ) != identity:
            _fail("RISK_OBSERVER_FILE_IDENTITY_CHANGED")
        for parent, name, child_fd, child_identity in chain:
            child = os.fstat(child_fd)
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISDIR(child.st_mode)
                or (int(child.st_dev), int(child.st_ino)) != child_identity
                or (int(linked.st_dev), int(linked.st_ino)) != child_identity
            ):
                _fail("RISK_OBSERVER_FILE_IDENTITY_CHANGED")
        if parent_fd is not None:
            parent_after = os.fstat(parent_fd)
            if (
                (int(parent_after.st_dev), int(parent_after.st_ino)) != parent_identity
                or not stat.S_ISDIR(parent_after.st_mode)
                or parent_after.st_uid != os.geteuid()
                or parent_after.st_mode & 0o077
            ):
                _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_bounded(path: Path, limit: int, *, private: bool) -> bytes:
    try:
        with _regular_file(path, size_limit=limit, private=private) as (fd, _identity):
            data = bytearray()
            while len(data) <= limit:
                block = os.read(fd, min(65_536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            if len(data) > limit:
                _fail("RISK_OBSERVER_FILE_UNAVAILABLE")
            return bytes(data)
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_FILE_UNAVAILABLE")


def _parse_json_integer(value: str) -> int:
    if len(value) > 64:
        _fail("RISK_OBSERVER_CONFIG_INVALID")
    return int(value)


def _reject_json_number(_value: str):
    _fail("RISK_OBSERVER_CONFIG_INVALID")


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("RISK_OBSERVER_CONFIG_INVALID")
        result[key] = value
    return result


def _parse_yaml(text: str):
    try:
        import yaml
    except Exception:
        _fail("RISK_OBSERVER_CONFIG_INVALID")

    class Loader(yaml.SafeLoader):
        def __init__(self, stream):
            super().__init__(stream)
            self.node_count = 0
            self.depth = 0

        def compose_node(self, parent, index):
            self.node_count += 1
            self.depth += 1
            if self.node_count > _YAML_NODE_LIMIT or self.depth > _YAML_DEPTH_LIMIT:
                _fail("RISK_OBSERVER_CONFIG_INVALID")
            try:
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1

        def construct_document(self, node):
            stack = [(node, 0, False)]
            ancestors = set()
            visits = 0
            while stack:
                current, depth, leaving = stack.pop()
                if leaving:
                    ancestors.remove(id(current))
                    continue
                visits += 1
                if visits > _YAML_NODE_LIMIT or depth > _YAML_DEPTH_LIMIT:
                    _fail("RISK_OBSERVER_CONFIG_INVALID")
                if id(current) in ancestors:
                    _fail("RISK_OBSERVER_CONFIG_INVALID")
                children = []
                if current.tag == "tag:yaml.org,2002:float":
                    _fail("RISK_OBSERVER_CONFIG_INVALID")
                if isinstance(current, yaml.nodes.MappingNode):
                    for key, value in current.value:
                        children.extend((key, value))
                elif isinstance(current, yaml.nodes.SequenceNode):
                    children.extend(current.value)
                if children:
                    ancestors.add(id(current))
                    stack.append((current, depth, True))
                    stack.extend((child, depth + 1, False) for child in reversed(children))
            return super().construct_document(node)

        def construct_mapping(self, node, deep=False):
            self.flatten_mapping(node)
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if type(key) is not str or key in result:
                    _fail("RISK_OBSERVER_CONFIG_INVALID")
                value = self.construct_object(value_node, deep=deep)
                result[key] = value
            return result

    try:
        loader = Loader(text)
        try:
            document = loader.get_single_data()
            return document
        finally:
            loader.dispose()
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_CONFIG_INVALID")


def _public_storage_directory(config_path: Path) -> Path:
    raw = _read_bounded(config_path, _CONFIG_LIMIT, private=False)
    try:
        text = raw.decode("utf-8", errors="strict")
        if text.lstrip().startswith("{"):
            document = json.loads(
                text,
                object_pairs_hook=_unique_json_pairs,
                parse_float=_reject_json_number,
                parse_constant=_reject_json_number,
                parse_int=_parse_json_integer,
            )
        else:
            document = _parse_yaml(text)
        if type(document) is not dict or document.get("schema") != _SERVICE_SCHEMA:
            _fail("RISK_OBSERVER_CONFIG_INVALID")
        storage = document.get("storage")
        if type(storage) is not dict or set(storage) != {"directory"}:
            _fail("RISK_OBSERVER_CONFIG_INVALID")
        value = storage["directory"]
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > 4096
            or len(value.encode("utf-8", errors="strict")) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "$" in value
            or value.startswith("~")
            or (os.name == "nt" and "%" in value)
        ):
            _fail("RISK_OBSERVER_CONFIG_INVALID")
        directory = Path(value)
        if not directory.is_absolute():
            directory = config_path.absolute().parent / directory
        return Path(os.path.abspath(directory))
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_CONFIG_INVALID")


def risk_database_path(config_path: str | os.PathLike[str]) -> Path:
    """Resolve only the current public JSON/YAML storage path subset."""
    try:
        directory = _public_storage_directory(Path(config_path))
        return Path(os.path.abspath(directory / "risk.sqlite"))
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_CONFIG_INVALID")


def _read_protected_marker(path: Path) -> tuple[bytes, tuple[int, int]]:
    try:
        with _regular_file(path, size_limit=_STOP_MARKER_LIMIT, private=True) as (fd, identity):
            data = os.read(fd, _STOP_MARKER_LIMIT + 1)
            if len(data) > _STOP_MARKER_LIMIT:
                _fail("RISK_OBSERVER_STOP_MARKER_INVALID")
            return data, identity
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_STOP_MARKER_UNAVAILABLE")


def create_owner_stop_marker(directory: str | os.PathLike[str]) -> Path:
    """Create the protected, nonempty marker; truncating it requests stop."""
    marker = Path(directory) / f"risk-{uuid4().hex}.stop"
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_STOP_MARKER_INITIAL)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            _windows_set_owner_only_acl(marker)
        else:
            os.chmod(marker, 0o600)
        content, _identity = _read_protected_marker(marker)
        if content != _STOP_MARKER_INITIAL:
            _fail("RISK_OBSERVER_STOP_MARKER_UNAVAILABLE")
        return marker
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_STOP_MARKER_UNAVAILABLE")


def _bound_schema_metadata(rows) -> tuple[tuple[str, str], ...]:
    if len(rows) > _SCHEMA_OBJECT_LIMIT:
        _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
    bounded = []
    total = 0
    for row in rows:
        if len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str:
            _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
        object_type, name = row
        try:
            type_bytes = len(object_type.encode("utf-8", errors="strict"))
            name_bytes = len(name.encode("utf-8", errors="strict"))
        except UnicodeError:
            _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
        if (
            len(object_type) > _SCHEMA_TYPE_CHAR_LIMIT
            or type_bytes > _SCHEMA_TYPE_BYTE_LIMIT
            or len(name) > _SCHEMA_NAME_CHAR_LIMIT
            or name_bytes > _SCHEMA_NAME_BYTE_LIMIT
        ):
            _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
        if object_type not in {"table", "index", "view", "trigger"}:
            _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
        total += type_bytes + name_bytes
        if total > _SCHEMA_METADATA_BYTE_LIMIT:
            _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
        bounded.append((object_type, name))
    if not any(kind == "table" and not name.startswith("sqlite_") for kind, name in bounded):
        _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
    return tuple(sorted(bounded))


def _schema_rows(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    try:
        rows = connection.execute(
            "SELECT type, name FROM sqlite_schema "
            "WHERE type IN ('table', 'index', 'view', 'trigger') LIMIT ?",
            (_SCHEMA_OBJECT_LIMIT + 1,),
        ).fetchmany(_SCHEMA_OBJECT_LIMIT + 1)
    except Exception:
        _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
    return _bound_schema_metadata(rows)


def _sqlite_uri_for_pinned_file(path: Path, fd: int, identity: tuple[int, int]) -> str:
    """Give SQLite a stable handle-based name, or fail closed if unavailable."""
    absolute = Path(os.path.abspath(path))
    if os.name == "nt":
        _windows_path_chain(absolute.parent, final_directory=True)
        check_fd, _kernel = _windows_open(absolute)
        try:
            current = _validate_windows_stamp(
                _windows_handle_stamp(check_fd), directory=False, size_limit=_DATABASE_LIMIT
            )
            if current != identity:
                _fail("RISK_OBSERVER_FILE_IDENTITY_CHANGED")
            _windows_private_file(absolute)
        finally:
            os.close(check_fd)
        # _storage_directory and _regular_file hold no-delete Win32 handles
        # throughout SQLite's pathname open; the owner-private directory ACL
        # prevents an untrusted actor from replacing the directory entry.
        return absolute.as_uri() + "?mode=ro"

    if os.name != "posix":
        _fail("RISK_OBSERVER_PINNED_OPEN_UNAVAILABLE")
    if sys.platform.startswith("linux"):
        roots = (Path("/proc/self/fd"),)
    elif sys.platform in {"darwin", "freebsd", "openbsd", "netbsd"}:
        roots = (Path("/dev/fd"),)
    else:
        _fail("RISK_OBSERVER_PINNED_OPEN_UNAVAILABLE")
    try:
        pinned = os.fstat(fd)
        for root in roots:
            candidate = root / str(fd)
            try:
                link = candidate.lstat()
                target = candidate.stat()
            except OSError:
                continue
            if (
                stat.S_ISLNK(link.st_mode)
                and stat.S_ISREG(target.st_mode)
                and (int(target.st_dev), int(target.st_ino)) == identity
                and (int(pinned.st_dev), int(pinned.st_ino)) == identity
                and target.st_uid == os.geteuid()
                and not target.st_mode & 0o077
                and target.st_size <= _DATABASE_LIMIT
            ):
                return candidate.as_uri() + "?mode=ro"
    except RiskObservationError:
        raise
    except OSError:
        _fail("RISK_OBSERVER_PINNED_OPEN_UNAVAILABLE")
    _fail("RISK_OBSERVER_PINNED_OPEN_UNAVAILABLE")


def _observe_database(path: Path) -> tuple[RiskObservation, tuple]:
    try:
        with (
            _storage_directory(path.parent) as (directory_fd, directory_identity),
            _regular_file(
                path,
                size_limit=_DATABASE_LIMIT,
                private=True,
                parent_fd=directory_fd if os.name == "posix" else None,
                parent_identity=directory_identity if os.name == "posix" else None,
            ) as (fd, identity),
        ):
            uri = _sqlite_uri_for_pinned_file(path, fd, identity)
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
                isolation_level=None,
            )
            callback_count = 0

            def progress() -> int:
                nonlocal callback_count
                callback_count += 1
                return int(callback_count > _SQLITE_MAX_PROGRESS_CALLBACKS)

            try:
                connection.set_progress_handler(progress, _SQLITE_PROGRESS_OPCODES)
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _SQLITE_VALUE_LIMIT)
                connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, _SQLITE_VALUE_LIMIT)
                connection.execute("PRAGMA query_only = ON")
                if connection.execute("PRAGMA query_only").fetchone() != (1,):
                    _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")
                schema_version = connection.execute("PRAGMA schema_version").fetchone()
                user_version = connection.execute("PRAGMA user_version").fetchone()
                schema = _schema_rows(connection)
            finally:
                connection.close()
        if (
            schema_version is None
            or user_version is None
            or type(schema_version[0]) is not int
            or type(user_version[0]) is not int
        ):
            _fail("RISK_OBSERVER_SCHEMA_UNAVAILABLE")
        signature = (identity, schema_version[0], user_version[0], schema)
        observation = RiskObservation(
            state="RISK_OBSERVER_OBSERVED",
            mode="OBSERVE_ONLY",
            storage_health="SQLITE_SCHEMA_READABLE",
            risk_readiness="NOT_ASSESSED",
            account_readiness="NOT_ASSESSED",
            market_readiness="NOT_ASSESSED",
            sqlite_schema_version=schema_version[0],
            sqlite_user_version=user_version[0],
            schema_object_count=len(schema),
            risk_schema_validation="NOT_VALIDATED",
        )
        return observation, signature
    except RiskObservationError:
        raise
    except Exception:
        _fail("RISK_OBSERVER_STORAGE_UNAVAILABLE")


class RiskObservationServiceHost:
    """Poll the existing SQLite file with finite, read-only observations."""

    def __init__(self, config_path: str | os.PathLike[str], *, stop_file: Path) -> None:
        self.database_path = risk_database_path(config_path)
        self.stop_file = Path(stop_file)
        initial, self._stop_identity = _read_protected_marker(self.stop_file)
        if initial not in (_STOP_MARKER_INITIAL, b""):
            _fail("RISK_OBSERVER_STOP_MARKER_INVALID")
        self._database_signature: tuple | None = None

    def stop_requested(self) -> bool:
        marker, identity = _read_protected_marker(self.stop_file)
        if identity != self._stop_identity:
            _fail("RISK_OBSERVER_STOP_MARKER_IDENTITY_CHANGED")
        if marker not in (_STOP_MARKER_INITIAL, b""):
            _fail("RISK_OBSERVER_STOP_MARKER_INVALID")
        return marker == b""

    def observe_once(self) -> RiskObservation:
        observation, signature = _observe_database(self.database_path)
        if self._database_signature is None:
            self._database_signature = signature
        elif signature != self._database_signature:
            if signature[0] != self._database_signature[0]:
                _fail("RISK_OBSERVER_STORAGE_IDENTITY_CHANGED")
            _fail("RISK_OBSERVER_SCHEMA_CHANGED")
        return observation

    def run(self, *, output: TextIO = sys.stdout) -> int:
        try:
            if self.stop_requested():
                _emit(output, _state_payload("RISK_OBSERVER_STOPPED"))
                return 0
            observation = self.observe_once()
            if self.stop_requested():
                _emit(output, _state_payload("RISK_OBSERVER_STOPPED"))
                return 0
            _emit(output, {**asdict(observation), **_state_payload("RISK_OBSERVER_READY")})
            while True:
                time.sleep(_POLL_INTERVAL_SECONDS)
                if self.stop_requested():
                    _emit(output, _state_payload("RISK_OBSERVER_STOPPED"))
                    return 0
                self.observe_once()
        except RiskObservationError as error:
            _emit(output, _state_payload("RISK_OBSERVER_FAILED_CLOSED", code=error.code))
            return 2


def _state_payload(state: str, *, code: str | None = None) -> dict[str, str]:
    result = {
        "state": state,
        "observer": "RISK_STORAGE_OBSERVER",
        "mode": "OBSERVE_ONLY",
        "risk_readiness": "NOT_ASSESSED",
        "account_readiness": "NOT_ASSESSED",
        "market_readiness": "NOT_ASSESSED",
    }
    if code is not None:
        result["code"] = code
    return result


def _emit(stream: TextIO, payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=stream, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe existing Risk SQLite storage only")
    parser.add_argument("--config", required=False)
    args = parser.parse_args(argv)
    if args.config is None:
        parser.print_help()
        return 0
    try:
        database_path = risk_database_path(args.config)
        _observe_database(database_path)
        marker = create_owner_stop_marker(Path(args.config).absolute().parent)
        print("RISK_OBSERVER_STOP_FILE " + marker.name, flush=True)
        return RiskObservationServiceHost(args.config, stop_file=marker).run()
    except RiskObservationError as error:
        _emit(sys.stderr, _state_payload("RISK_OBSERVER_FAILED_CLOSED", code=error.code))
        return 2


__all__ = [
    "RiskObservation",
    "RiskObservationError",
    "RiskObservationServiceHost",
    "create_owner_stop_marker",
    "main",
    "risk_database_path",
]
