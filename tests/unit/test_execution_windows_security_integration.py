"""Windows integration checks for the Execution security-gate parity layer.

Skipped off Windows.  These verify that the Execution modules reach the
Windows primitives with *equal* strength (never a relaxed gate) and that the
sleep-inclusive lease clock / lock contention semantics are preserved.
They do not claim account, dispatch or recovery authority.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bt_api_py import _win32_security as w
from bt_api_py.execution_read_service_host import ReadServiceError, _directory

pytestmark = pytest.mark.skipif(not w.WINDOWS, reason="Windows-only integration checks")


def _make_private(path: Path) -> None:
    """Give a file a POSIX-0600-equivalent DACL (the foundation refuses loose ones)."""
    import win32con
    import win32security

    sd = win32security.GetFileSecurity(
        os.fspath(path), win32security.OWNER_SECURITY_INFORMATION)
    dacl = win32security.ACL()
    for text in ('S-1-5-18', 'S-1-5-32-544'):
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL,
                                 win32security.ConvertStringSidToSid(text))
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL,
                             sd.GetSecurityDescriptorOwner())
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(os.fspath(path), win32security.DACL_SECURITY_INFORMATION, sd)


def test_journal_clock_includes_sleep_and_is_monotonic():
    import bt_api_execution.account_journal_host as host_module

    before = host_module._sleep_inclusive_clock_ns()
    assert type(before) is int
    time.sleep(0.05)
    after = host_module._sleep_inclusive_clock_ns()
    assert 40_000_000 <= after - before < 1_000_000_000


def test_journal_clock_failure_fails_closed(monkeypatch):
    import bt_api_execution.account_journal_host as host_module
    from bt_api_execution.account_journal_host import AccountJournalHostError

    def unavailable():
        raise w.Win32SecurityUnavailable("INTAKE_CLOCK_UNAVAILABLE")

    monkeypatch.setattr(w, "sleep_inclusive_clock_ns", unavailable)
    with pytest.raises(AccountJournalHostError, match="INTAKE_CLOCK_UNAVAILABLE"):
        host_module._sleep_inclusive_clock_ns()


def test_journal_lock_contention_is_rejected_and_release_restores_it(tmp_path):
    import bt_api_execution.account_journal_host as host_module

    path = tmp_path / "bootstrap.lock"
    first = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    second = os.open(path, os.O_RDWR)
    try:
        host_module._flock_exclusive_nonblocking(first)
        with pytest.raises(BlockingIOError):
            host_module._flock_exclusive_nonblocking(second)
        import msvcrt

        os.lseek(first, 0, os.SEEK_SET)
        msvcrt.locking(first, msvcrt.LK_UNLCK, 1)
        host_module._flock_exclusive_nonblocking(second)
        os.lseek(second, 0, os.SEEK_SET)
        msvcrt.locking(second, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(first)
        os.close(second)


def test_directory_accepts_current_user_and_rejects_others_writable(tmp_path, monkeypatch):
    path = Path(os.path.abspath(tmp_path))

    monkeypatch.setattr(
        w, "inspect_owner",
        lambda _p: w.OwnerInfo("S-1-5-21-test-owner", True, False),
    )
    _directory(path)  # current user, no other-writable ACE -> accepted

    monkeypatch.setattr(
        w, "inspect_owner",
        lambda _p: w.OwnerInfo("S-1-1-0", False, True),
    )
    with pytest.raises(ReadServiceError, match="INVALID_STORAGE"):
        _directory(path)  # others-writable / not current user -> refused


def test_directory_refuses_reparse_point(tmp_path, monkeypatch):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires privilege on this host")
    monkeypatch.setattr(
        w, "inspect_owner",
        lambda _p: w.OwnerInfo("S-1-5-21-test-owner", True, False),
    )
    with pytest.raises(ReadServiceError, match="INVALID_STORAGE"):
        _directory(link)


def test_protected_read_allows_trusted_file_and_refuses_tampered_owner(tmp_path, monkeypatch):
    from bt_api_py.monitor_service_config import read_bounded

    path = tmp_path / "config.json"
    path.write_bytes(b'{"ok": true}')
    _make_private(path)
    assert read_bounded(path, 4096, protected=True) == b'{"ok": true}'

    monkeypatch.setattr(
        w, "inspect_owner",
        lambda _p: w.OwnerInfo("S-1-1-0", False, True),
    )
    with pytest.raises(ValueError, match="FILE_UNAVAILABLE"):
        read_bounded(path, 4096, protected=True)


def test_protected_read_refuses_oversize_and_missing(tmp_path):
    from bt_api_py.monitor_service_config import read_bounded

    path = tmp_path / "config.json"
    path.write_bytes(b"x" * 64)
    for protected in (False, True):
        with pytest.raises(ValueError, match="FILE_UNAVAILABLE"):
            read_bounded(path, 8, protected=protected)
    with pytest.raises(ValueError, match="FILE_UNAVAILABLE"):
        read_bounded(tmp_path / "missing", 4096, protected=True)


def test_registry_prepare_is_exclusive_and_refuses_reparse(tmp_path):
    from types import SimpleNamespace

    from bt_api_py.execution_parent_service_host import ParentServiceError, _prepare_registry

    registry = tmp_path / "registry.db"
    config = SimpleNamespace(registry_path=registry)
    _prepare_registry(config)
    assert registry.exists()
    _prepare_registry(config)  # an existing regular file is not recreated

    link = tmp_path / "link.db"
    try:
        os.symlink(registry, link, target_is_directory=False)
    except OSError:
        pytest.skip("symlink creation requires privilege on this host")
    with pytest.raises(ParentServiceError, match="INVALID_STORAGE"):
        _prepare_registry(SimpleNamespace(registry_path=link))


def test_private_root_hosts_fail_closed_on_windows(tmp_path):
    """Unsafe roots are refused; the Host now has Windows fd parity.

    AccountJournalHost has a Windows fd-equivalence layer (_win32_fdops), so a
    default-DACL (non owner-only) root is refused as INVALID_STORAGE rather
    than PLATFORM_UNSUPPORTED.  FreshSubmissionIntake still has no equivalent
    and stays fail-closed with PLATFORM_UNSUPPORTED.
    """
    from bt_api_execution.account_journal_host import (
        AccountJournalHost,
        AccountJournalHostError,
    )
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_execution.submission_intake import FreshSubmissionIntake, SubmissionIntakeError

    scope = ExecutionScope("FAKE", "test", "synthetic-account", "synthetic-strategy")
    # Explicitly grant Everyone write so the root is untrusted regardless of
    # the ambient tmp_path DACL (which the win_acl_fixture plugin may narrow).
    import win32con
    import win32security
    sd = win32security.GetFileSecurity(str(tmp_path), win32security.DACL_SECURITY_INFORMATION)
    dacl = sd.GetSecurityDescriptorDacl()
    if dacl is None:
        dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, win32con.GENERIC_ALL,
        win32security.ConvertStringSidToSid("S-1-1-0"))
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(str(tmp_path), win32security.DACL_SECURITY_INFORMATION, sd)
    with pytest.raises(AccountJournalHostError, match="INVALID_STORAGE|PLATFORM_UNSUPPORTED"):
        AccountJournalHost(root=str(tmp_path), scope=scope, storage_id="d9044d18-dc74-4cb4-a574-3f5d8e5ca7a5")
    with pytest.raises(SubmissionIntakeError, match="PLATFORM_UNSUPPORTED"):
        FreshSubmissionIntake(root=str(tmp_path), targets={})
