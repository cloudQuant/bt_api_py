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


def test_local_gateway_and_risk_web_hosts_reregister_after_socket_retirement(monkeypatch):
    """Simulate ZmqServiceClient retiring Socket #1: service.renew stays alive
    (healthy=True) while authenticated_peer becomes None. Both LocalGatewayWebHost
    and LocalRiskWebHost must cleanly close and re-register a fresh runtime, and
    must fail closed when close reports uncertain cleanup."""
    import base64
    import hashlib
    import zmq

    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py import gateway_web_local_host as gw_mod
    from bt_api_py import risk_web_local_host as risk_mod
    from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot

    srv_pub, _srv_sec = zmq.curve_keypair()
    cli_pub, cli_sec = zmq.curve_keypair()
    tok = "a" * 64
    tok_hash = hashlib.sha256(tok.encode("ascii")).hexdigest()
    root = _make_private_dir()

    # --- 1) LocalGatewayWebHost ---
    gw_identity = WireIdentity(WireRole.APPLICATION_CLIENT, "op-gw", "alice")
    gw_peer = WireIdentity(WireRole.SERVICE, "gw-svc-p", "gw-svc")
    gw_scope = WireScope("domain", account_ref="acct-1")
    created_gw = []

    class FakeGwRuntime:
        def __init__(self, _factory, *, require_unregister_ack=False):
            self.idx = len(created_gw)
            self.require_unregister_ack = require_unregister_ack
            self.state = "new"
            self.thread_alive = True
            self.healthy = True
            self.peer_ok = True
            self.close_uncertain = False
            self.closed_calls = 0
            created_gw.append(self)

        def status(self):
            b = BindingSnapshot(
                registration_id=f"reg-{self.idx}",
                service_epoch="ep-1",
                active=True,
                mount="application.direct_write",
                service_id="gw-svc",
                instance_id="gw-1",
                scope_ref="scope-1",
                scope=gw_scope,
                operations=frozenset({"direct.control.identity", "direct.control.list"}),
                required=True,
                authenticated_peer=gw_peer if self.peer_ok else None,
                server_key_fingerprint="b" * 64 if self.peer_ok else None,
            )
            return RuntimeSnapshot(
                self.state, self.thread_alive, self.healthy, None, (b,), 0,
                1 if self.close_uncertain else 0, gw_identity, f"run-{self.idx}",
            )

        def start(self, *, timeout_seconds=10):
            self.state = "running"
            return self.status()

        def close(self, *, timeout_seconds=10):
            self.closed_calls += 1
            self.state = "closed"
            self.thread_alive = False
            return RuntimeSnapshot(
                "closed", False, False, None, (), 0,
                1 if self.close_uncertain else 0, gw_identity,
            )

    monkeypatch.setattr(gw_mod, "ThreadedServiceClientRuntime", FakeGwRuntime)
    gw_cfg = _write_protected(
        root / "gw_web.json",
        json.dumps({
            "schema_version": 1,
            "origin": "http://127.0.0.1:8791",
            "receipt_key_base64": base64.b64encode(b"k" * 32).decode("ascii"),
            "receipt_key_id": "k1",
            "operators": [{
                "principal_ref": "op-gw",
                "actor_ref": "alice",
                "login_token_sha256": tok_hash,
                "scope_id": "desk",
                "scope_ref": "scope-1",
                "execution_domain_ref": "domain",
                "account_ref": "acct-1",
                "strategy_ref": None,
                "service_id": "gw-svc",
                "service_principal_ref": "gw-svc-p",
                "instance_id": "gw-1",
                "endpoint": "tcp://127.0.0.1:59991",
                "server_public_key": srv_pub.decode("ascii"),
                "client_public_key": cli_pub.decode("ascii"),
                "client_secret_key": cli_sec.decode("ascii"),
                "operations": ["direct.control.identity", "direct.control.list"],
            }],
        }).encode("utf-8"),
    )
    gw_host = gw_mod.LocalGatewayWebHost(gw_cfg).start()
    try:
        op = gw_host._operators[0]
        session = SimpleNamespace(principal_ref="op-gw", actor_ref="alice")
        rt0 = gw_host._resolve_runtime(session, op.web_scope)
        assert rt0 is created_gw[0] and len(created_gw) == 1

        # Simulate retired socket: healthy=True, state="running", but authenticated_peer=None.
        rt0.peer_ok = False
        rt1 = gw_host._resolve_runtime(session, op.web_scope)
        assert rt1 is created_gw[1]
        assert rt0.closed_calls == 1
        assert rt1.status().bindings[0].authenticated_peer == gw_peer

        # Uncertain cleanup must fail closed without creating a replacement runtime.
        rt1.peer_ok = False
        rt1.close_uncertain = True
        rt_still = gw_host._resolve_runtime(session, op.web_scope)
        assert rt_still is rt1 and len(created_gw) == 2
        rt1.close_uncertain = False
    finally:
        gw_host.close()

    # --- 2) LocalRiskWebHost ---
    risk_identity = WireIdentity(WireRole.APPLICATION_CLIENT, "op-risk", "risk-actor")
    risk_peer = WireIdentity(WireRole.SERVICE, "risk-svc-p", "risk-svc")
    risk_scope = WireScope("domain", account_ref="acct-1")
    created_risk = []

    class FakeRiskRuntime:
        def __init__(self, _factory, *, require_unregister_ack=False):
            self.idx = len(created_risk)
            self.require_unregister_ack = require_unregister_ack
            self.state = "new"
            self.thread_alive = True
            self.healthy = True
            self.peer_ok = True
            self.close_uncertain = False
            self.closed_calls = 0
            created_risk.append(self)

        def status(self):
            b = BindingSnapshot(
                registration_id=f"rreg-{self.idx}",
                service_epoch="ep-1",
                active=True,
                mount=risk_mod.RISK_ADMIN_MOUNT,
                service_id="risk-svc",
                instance_id="risk-1",
                scope_ref="risk-scope",
                scope=risk_scope,
                operations=frozenset({"risk.admin.status"}),
                required=True,
                authenticated_peer=risk_peer if self.peer_ok else None,
                server_key_fingerprint="c" * 64 if self.peer_ok else None,
            )
            return RuntimeSnapshot(
                self.state, self.thread_alive, self.healthy, None, (b,), 0,
                1 if self.close_uncertain else 0, risk_identity, f"rrun-{self.idx}",
            )

        def start(self, *, timeout_seconds=10):
            self.state = "running"
            return self.status()

        def call_bound(self, *args, **kwargs):
            return None

        def close(self, *, timeout_seconds=10):
            self.closed_calls += 1
            self.state = "closed"
            self.thread_alive = False
            return RuntimeSnapshot(
                "closed", False, False, None, (), 0,
                1 if self.close_uncertain else 0, risk_identity,
            )

    monkeypatch.setattr(risk_mod, "ThreadedServiceClientRuntime", FakeRiskRuntime)
    risk_cfg = _write_protected(
        root / "risk_web.json",
        json.dumps({
            "schema_version": 1,
            "origin": "http://127.0.0.1:8792",
            "operator": {
                "principal_ref": "op-risk",
                "actor_ref": "risk-actor",
                "login_token_sha256": tok_hash,
                "execution_domain_ref": "domain",
                "account_ref": "acct-1",
                "scope_ref": "risk-scope",
                "service_id": "risk-svc",
                "service_principal_ref": "risk-svc-p",
                "instance_id": "risk-1",
                "endpoint": "tcp://127.0.0.1:59992",
                "server_public_key": srv_pub.decode("ascii"),
                "client_public_key": cli_pub.decode("ascii"),
                "client_secret_key": cli_sec.decode("ascii"),
                "operations": ["risk.admin.status"],
            },
        }).encode("utf-8"),
    )
    risk_host = risk_mod.LocalRiskWebHost(risk_cfg).start()
    try:
        rt0 = risk_host._ensure_runtime()
        assert rt0 is created_risk[0] and len(created_risk) == 1

        rt0.peer_ok = False
        rt1 = risk_host._ensure_runtime()
        assert rt1 is created_risk[1]
        assert rt0.closed_calls == 1
        assert rt1.status().bindings[0].authenticated_peer == risk_peer

        rt1.peer_ok = False
        rt1.close_uncertain = True
        rt_still = risk_host._ensure_runtime()
        assert rt_still is rt1 and len(created_risk) == 2
        rt1.close_uncertain = False
    finally:
        risk_host.close()
