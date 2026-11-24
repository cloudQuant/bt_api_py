"""Windows parity regression tests for operator Web hosts and Execution read host.

Skipped off Windows. Verifies local Win32 security and locking invariants only;
does not claim any account or remote service capability.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py import _win32_security as w

pytestmark = pytest.mark.skipif(not w.WINDOWS, reason="Windows-only parity tests")


def _make_private_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix="bt_win32_parity_"))
    w._set_owner_only_acl(path)
    return path


def _write_protected(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    w._set_owner_only_acl(path)
    return path


def test_read_bounded_windows_with_stamp_and_share_write():
    root = _make_private_dir()
    path = _write_protected(root / "cfg.json", b'{"ok":true}')

    raw, stamp1 = w.read_bounded_windows_with_stamp(path, 4096, protected=True)
    assert raw == b'{"ok":true}'
    assert len(stamp1) == 8

    # Another writer holding the file open with write share must not block
    # POSIX-equivalent read-only nofollow opens.
    with open(path, "r+b") as writer:
        fd, handle = w.open_nofollow(path, share_write=True)
        try:
            assert os.read(fd, 64) == b'{"ok":true}'
            assert w.handle_stamp(handle) == stamp1
        finally:
            os.close(fd)
        writer.seek(0)
        writer.write(b'{"ok":false}')
        writer.flush()

    raw2, stamp2 = w.read_bounded_windows_with_stamp(path, 4096, protected=True)
    assert raw2 == b'{"ok":false}'
    assert stamp2 != stamp1


def test_open_identity_handle_file_and_directory():
    root = _make_private_dir()
    file_path = _write_protected(root / "data.bin", b"abc")

    dir_handle = w.open_identity_handle(root, directory=True)
    file_handle = w.open_identity_handle(file_path, directory=False)
    try:
        dir_stamp = w.handle_stamp(dir_handle)
        file_stamp = w.handle_stamp(file_handle)
        assert dir_stamp[:2] != file_stamp[:2]
        assert file_stamp[5] == 3
    finally:
        w.close_handle(file_handle)
        w.close_handle(dir_handle)


def test_gateway_web_protected_bytes_windows_parity():
    from bt_api_py.gateway_web_local_host import _protected_bytes

    root = _make_private_dir()
    cfg = _write_protected(root / "web_cfg.json", b'{"schema_version":1}')

    raw, stamp1 = _protected_bytes(cfg)
    assert raw == b'{"schema_version":1}'
    assert len(stamp1) == 8

    # Unprotected file (default inherited DACL) must fail closed.
    unprotected = Path(tempfile.mkdtemp(prefix="bt_win32_open_")) / "open.json"
    unprotected.write_bytes(b'{"schema_version":1}')
    with pytest.raises(ValueError, match="PROTECTED_FILE_UNAVAILABLE"):
        _protected_bytes(unprotected)

    # Relative path and parent traversal must fail closed.
    with pytest.raises(ValueError, match="PROTECTED_FILE_UNAVAILABLE"):
        _protected_bytes(Path("relative.json"))
    with pytest.raises(ValueError, match="PROTECTED_FILE_UNAVAILABLE"):
        _protected_bytes(root / ".." / root.name / "web_cfg.json")

    # In-place modification must change the stamp.
    cfg.write_bytes(b'{"schema_version":2}')
    w._set_owner_only_acl(cfg)
    raw2, stamp2 = _protected_bytes(cfg)
    assert raw2 == b'{"schema_version":2}'
    assert stamp2 != stamp1


def test_execution_management_read_audit_windows_parity():
    from bt_api_execution.local_parent_snapshot import LocalParentSnapshot
    from bt_api_py.execution_management_audit import (
        ExecutionManagementAuditError,
        ExecutionManagementReadAudit,
        ManagementReadIdentity,
    )

    root = _make_private_dir()
    audit_dir = root / "audit"
    audit_dir.mkdir()
    w._set_owner_only_acl(audit_dir)

    identity = ManagementReadIdentity(
        service_instance="exec-read-win-1",
        service_epoch="epoch-1",
        caller_role="application_client",
        principal_ref="web-exec-reader",
        actor_ref="web-exec-actor",
        account_ref="acct-1",
        strategy_ref="strat-1",
        domain_ref="domain",
        policy_revision="grant-v1",
    )
    snap = LocalParentSnapshot("UNKNOWN")

    audit = ExecutionManagementReadAudit(audit_dir, max_attempts=10)
    try:
        out1 = audit.disclose_after_audit(
            identity=identity,
            query_id="q-win-1",
            parent_ref="parent-1",
            snapshot=snap,
            operation_version=1,
        )
        out2 = audit.disclose_after_audit(
            identity=identity,
            query_id="q-win-2",
            parent_ref="parent-1",
            snapshot=snap,
            operation_version=1,
        )
        assert out1 == snap
        assert out2 == snap
    finally:
        audit.close()

    # Reopening the existing audit root must verify handle identity and allow further writes.
    audit2 = ExecutionManagementReadAudit(audit_dir, max_attempts=10)
    try:
        out3 = audit2.disclose_after_audit(
            identity=identity,
            query_id="q-win-3",
            parent_ref="parent-1",
            snapshot=snap,
            operation_version=1,
        )
        assert out3 == snap
    finally:
        audit2.close()

    # Verify 3 audit rows persisted.
    db_path = audit_dir / "execution-management-read.sqlite3"
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_attempts").fetchone()[0] == 3

    # Existing unprotected directory must fail closed.
    unprotected_dir = Path(tempfile.mkdtemp(prefix="bt_win32_audit_open_"))
    with pytest.raises(ExecutionManagementAuditError):
        ExecutionManagementReadAudit(unprotected_dir, max_attempts=10)


def test_execution_read_check_existing_with_borrowed_locked_owner_on_windows():
    from bt_api_execution.account_journal_host import AccountJournalHost
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_py.execution_read_service_host import ReadServiceError, _check_existing

    root = _make_private_dir()
    journal_root = root / "journal"
    journal_root.mkdir()
    w._set_owner_only_acl(journal_root)

    scope = ExecutionScope(
        provider="ctp",
        environment="simnow",
        account_ref="acct-win-read-1",
        strategy_id="strat-win-read-1",
    )
    import uuid

    storage_id = str(uuid.uuid4())
    host = AccountJournalHost(
        root=str(journal_root),
        scope=scope,
        storage_id=storage_id,
        writer_mode="single_host_cooperative",
    )
    try:
        cfg = SimpleNamespace(
            journal_root=journal_root,
            local_scope=scope,
            storage_id=storage_id,
        )
        # While AccountJournalHost holds its mandatory Win32 byte-range lock on
        # the .owner marker, _check_existing with borrowed=host must pass.
        _check_existing(cfg, borrowed=host)

        # Wrong storage_id must still fail closed even when borrowed.
        bad_cfg = SimpleNamespace(
            journal_root=journal_root,
            local_scope=scope,
            storage_id="wrong-storage-id",
        )
        with pytest.raises(ReadServiceError, match="INVALID_STORAGE"):
            _check_existing(bad_cfg, borrowed=host)
    finally:
        host.close()
