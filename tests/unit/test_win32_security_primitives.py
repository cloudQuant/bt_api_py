"""Windows security-primitive parity tests.

Skipped off Windows.  These verify the *local invariants* the service start
checks rely on, not any account or authority fact.  Never use them to claim
that a real service or account capability is available.
"""

from __future__ import annotations

import os
import stat
import time

import pytest

from bt_api_py import _win32_security as w

pytestmark = pytest.mark.skipif(not w.WINDOWS, reason="Windows-only primitives")


def test_clock_includes_sleep_and_is_monotonic():
    before = w.sleep_inclusive_clock_ns()
    time.sleep(0.05)
    after = w.sleep_inclusive_clock_ns()
    assert after > before
    # GetTickCount64 includes sleep; a purely uptime-based clock would also
    # advance here, so assert the sample is a plausible ms-derived upper bound.
    assert (after - before) >= 40_000_000  # advanced with wall time (>=40 ms)
    assert (after - before) < 1_000_000_000  # sanity: not a wrap/bad sample


def test_clock_monotonic_non_decreasing():
    # GetTickCount64 has ms resolution; equal samples are valid.  The clock
    # must never go backwards within one process (lease fencing invariant).
    samples = [w.sleep_inclusive_clock_ns() for _ in range(5)]
    assert all(b >= a for a, b in zip(samples, samples[1:]))
    time.sleep(0.05)
    assert w.sleep_inclusive_clock_ns() > samples[-1]


def test_nofollow_open_and_handle_stamp(tmp_path):
    path = tmp_path / "resource.json"
    path.write_text("{}", encoding="utf-8")
    fd, handle = w.open_nofollow(path)
    try:
        assert os.read(fd, 2) == b"{}"
        stamp = w.handle_stamp(handle)
        assert stamp[2] == stat.S_IFREG
        assert stamp[4] >= 1  # link count exposed on Windows
    finally:
        os.close(fd)


def test_nofollow_rejects_directory(tmp_path):
    with pytest.raises(w.Win32SecurityUnavailable):
        w.open_nofollow(tmp_path)


def test_reparse_point_detection(tmp_path):
    target = tmp_path / "target"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=False)
    except OSError:
        pytest.skip("symlink creation requires privilege on this host")
    assert w.is_reparse_point(link) is True
    assert w.is_reparse_point(target) is False
    with pytest.raises(w.Win32SecurityUnavailable):
        w.open_nofollow(link)


def _owner_only(path):
    """Set an owner-only DACL: the Windows equivalent of POSIX mode 0600."""
    import win32con
    import win32security

    sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
    owner = sd.GetSecurityDescriptorOwner()
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        win32con.GENERIC_ALL,
        owner,
    )
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, sd)


def test_owner_of_own_file():
    assert w.has_privileges_available() is True
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "key.bin"
    path.write_bytes(b"x")
    info = w.inspect_owner(path)
    assert info.is_current_user is True
    # Default Windows DACL grants the user's primary group write access; POSIX
    # 0600 would not. A protected file must carry an owner-only DACL.
    assert info.others_writable is True
    _owner_only(path)
    strict = w.inspect_owner(path)
    assert strict.is_current_user is True
    assert strict.others_writable is False


def test_read_bounded_windows_modes(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(b"key: value\n")
    assert w.read_bounded_windows(path, 1048576, protected=False) == b"key: value\n"
    # Protected read requires the POSIX-0600 equivalent (owner-only DACL).
    _owner_only(path)
    assert w.read_bounded_windows(path, 1048576, protected=True) == b"key: value\n"


def test_read_bounded_windows_refuses_oversize_and_missing(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(b"key: value\n")
    with pytest.raises(w.Win32SecurityUnavailable):
        w.read_bounded_windows(path, 3, protected=False)
    with pytest.raises(w.Win32SecurityUnavailable):
        w.read_bounded_windows(tmp_path / "missing", 100, protected=False)


def test_calendar_fdops_stamps_match_posix_stat(tmp_path):
    """Regression: fdops.fstat timestamps must use the Unix epoch, not raw
    FILETIME (1601), so TOCTOU stamp comparisons against os.lstat agree."""
    from bt_api_py import _calendar_fdops as fdops

    path = tmp_path / "sample.json"
    path.write_bytes(b'{"a": 1}')
    native = path.lstat()
    fd = fdops.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        handled = fdops.fstat(fd)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
            assert getattr(handled, field) == getattr(native, field), field
        # Sanity: the ns value must be near the present, not ~369y in the future.
        assert abs(native.st_mtime_ns - time.time() * 1_000_000_000) < 60 * 1_000_000_000
    finally:
        fdops.close(fd)
