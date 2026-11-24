"""Windows parity of the Gateway storage-isolation gate (iteration 67).

Skipped off Windows.  The tests build a genuinely isolated directory (owner +
SYSTEM/Administrators only, no inherited ACEs) and prove that the Windows
branch accepts it and refuses reparse points, other-principal write ACEs and
foreign owners.  No path, SID or account value is ever asserted or printed.
"""

from __future__ import annotations

import importlib
import os
import subprocess

import pytest

from bt_api_py import _win32_security as w32
from bt_api_py.gateway_storage_trust import (
    GatewayStorageTrustError,
    assert_gateway_storage_isolated,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not w32.has_privileges_available(),
    reason="Windows security primitives required",
)


def _sec():
    # Imported lazily so collection on POSIX never touches pywin32.
    return importlib.import_module("win32security")


def _set_flags():
    sec = _sec()
    return (
        sec.DACL_SECURITY_INFORMATION
        | sec.OWNER_SECURITY_INFORMATION
        | sec.GROUP_SECURITY_INFORMATION
        | sec.PROTECTED_DACL_SECURITY_INFORMATION
    )


def _sid(text):
    return _sec().ConvertStringSidToSid(text)


def _set_clean_dacl(path):
    """Owner + SYSTEM + Administrators full control, no inherited ACEs."""
    sec = _sec()
    owner = _sid(w32.current_user_sid())
    dacl = sec.ACL()
    for sid in (owner, _sid("S-1-5-18"), _sid("S-1-5-32-544")):
        dacl.AddAccessAllowedAce(sec.ACL_REVISION, 0x1F01FF, sid)
    sd = sec.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorOwner(owner, False)
    sd.SetSecurityDescriptorGroup(owner, False)
    sd.SetSecurityDescriptorDacl(1, dacl, False)
    sec.SetFileSecurity(os.fspath(path), _set_flags(), sd)


def _add_everyone_write(path):
    sec = _sec()
    sd = sec.GetFileSecurity(os.fspath(path), _set_flags())
    dacl = sd.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(sec.ACL_REVISION, 0x0002, _sid("S-1-1-0"))  # FILE_WRITE_DATA
    sd.SetSecurityDescriptorDacl(1, dacl, False)
    sec.SetFileSecurity(os.fspath(path), _set_flags(), sd)


def _isolated(tmp_path):
    root = tmp_path / "gateway"
    root.mkdir()
    _set_clean_dacl(root)
    return root


def _junction(link, target):
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
        capture_output=True,
    )
    if result.returncode != 0 or not os.path.exists(link):
        pytest.skip("junction creation unavailable on this host")


def _code(directories, files=(), **kwargs):
    with pytest.raises(GatewayStorageTrustError) as error:
        assert_gateway_storage_isolated(directories, files, **kwargs)
    return error.value.code


def test_windows_isolated_directory_and_sqlite_files_pass(tmp_path):
    root = _isolated(tmp_path)
    journal = root / "orders.sqlite"
    journal.write_bytes(b"")
    (root / "orders.sqlite-wal").write_bytes(b"")
    assert_gateway_storage_isolated([root], [journal, root / "absent.sqlite"])


def test_windows_reparse_entry_and_directory_refused(tmp_path):
    root = _isolated(tmp_path)
    link = root / "junc"
    _junction(link, root)  # a junction inside the directory is an untrusted entry
    try:
        assert _code([root]) == "STORAGE_SYMLINK"
    finally:
        os.rmdir(link)
    outer = tmp_path / "outer"
    outer.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    as_dir = outer / "linked"
    _junction(as_dir, target)  # junction used as the storage directory itself
    try:
        assert _code([as_dir]) == "STORAGE_SYMLINK"
    finally:
        os.rmdir(as_dir)


def test_windows_others_write_ace_refused(tmp_path):
    root = _isolated(tmp_path)
    _add_everyone_write(root)
    assert _code([root]) == "STORAGE_WRITABLE_BY_OTHERS"
    journal = root / "orders.sqlite"
    journal.write_bytes(b"")
    _add_everyone_write(journal)
    assert _code([root], [journal]) == "STORAGE_WRITABLE_BY_OTHERS"


def test_windows_owner_mismatch_refused(tmp_path):
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    foreign = os.path.join(system_root, "win.ini")  # shipped, owner != current user
    if not os.path.isfile(foreign) or w32.inspect_owner(
        foreign, others_may_write_check=False
    ).is_current_user:
        pytest.skip("no foreign-owned system file available")
    assert _code([], [foreign]) == "STORAGE_OWNER_MISMATCH"


def test_windows_unsupported_platform_and_explicit_euid_refused(monkeypatch, tmp_path):
    root = _isolated(tmp_path)
    assert _code([root], euid=123) == "STORAGE_TRUST_INVALID"
    monkeypatch.setattr(w32, "has_privileges_available", lambda: False)
    assert _code([root]) == "STORAGE_TRUST_UNSUPPORTED_PLATFORM"


def test_windows_error_codes_carry_no_paths(tmp_path):
    root = _isolated(tmp_path)
    _add_everyone_write(root)
    with pytest.raises(GatewayStorageTrustError) as error:
        assert_gateway_storage_isolated([root])
    assert str(tmp_path) not in str(error.value)
    assert str(error.value) == error.value.code
